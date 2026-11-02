from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, Request, Response, UploadFile, status

from app.api.deps import DB, AdminAuth, AgentAuth, ViewerAuth
from app.core.config import settings
from app.core.errors import AppError
from app.core.rate_limit import enforce
from app.rag import service as rag_service
from app.schemas.knowledge import (
    ChunkPreview,
    DocumentDetail,
    DocumentOut,
    KnowledgeOverview,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from app.services import audit_service

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
READ_CHUNK = 1024 * 1024


@router.get("/documents", response_model=KnowledgeOverview)
async def list_documents(auth: ViewerAuth, db: DB) -> KnowledgeOverview:
    docs = await rag_service.list_documents(db, auth.tenant_id)
    counts = await rag_service.stats(db, auth.tenant_id)
    return KnowledgeOverview(
        documents=[DocumentOut.model_validate(d) for d in docs],
        total_documents=counts["documents"],
        total_chunks=counts["chunks"],
        vector_backend="pgvector" if settings.uses_pgvector else "in-process",
        embedding_model=settings.embedding_model,
    )


@router.post(
    "/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    summary=f"Upload a PDF, DOCX, TXT or MD document (max {settings.upload_max_mb} MB)",
)
async def upload_document(
    request: Request,
    auth: AdminAuth,
    db: DB,
    file: UploadFile = File(...),
    title: str | None = Form(default=None, max_length=300),
) -> DocumentOut:
    await enforce("kb-upload", str(auth.tenant_id), 20)
    # Read in chunks and stop as soon as the cap is exceeded (never buffer an oversized upload).
    data = bytearray()
    while chunk := await file.read(READ_CHUNK):
        data.extend(chunk)
        if len(data) > settings.upload_max_bytes:
            raise AppError(f"File exceeds the {settings.upload_max_mb} MB limit", status_code=413)
    document = await rag_service.ingest_document(
        db,
        tenant_id=auth.tenant_id,
        filename=file.filename or "document",
        content=bytes(data),
        title=title,
        uploaded_by_id=auth.user.id,
    )
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="knowledge.upload",
        resource_type="document",
        resource_id=document.id,
        actor=auth.user,
        request=request,
        details={
            "filename": document.filename,
            "chunks": document.chunk_count,
            "truncated": document.truncated,
        },
    )
    await db.commit()
    await db.refresh(document)
    await rag_service.bump_kb_version(auth.tenant_id)
    return DocumentOut.model_validate(document)


@router.get("/documents/{document_id}", response_model=DocumentDetail)
async def get_document(document_id: uuid.UUID, auth: ViewerAuth, db: DB) -> DocumentDetail:
    doc = await rag_service.get_document(db, auth.tenant_id, document_id)
    chunks = await rag_service.document_chunks(db, auth.tenant_id, document_id, limit=20)
    return DocumentDetail(
        **DocumentOut.model_validate(doc).model_dump(),
        chunks=[
            ChunkPreview(chunk_index=c.chunk_index, content=c.content[:600], meta=c.meta) for c in chunks
        ],
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(document_id: uuid.UUID, request: Request, auth: AdminAuth, db: DB) -> Response:
    doc = await rag_service.get_document(db, auth.tenant_id, document_id)
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="knowledge.delete",
        resource_type="document",
        resource_id=doc.id,
        actor=auth.user,
        request=request,
        details={"filename": doc.filename},
    )
    await rag_service.delete_document(db, doc)
    await db.commit()
    await rag_service.bump_kb_version(auth.tenant_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/search", response_model=SearchResponse, summary="Test retrieval against the knowledge base")
async def search(body: SearchRequest, auth: AgentAuth, db: DB) -> SearchResponse:
    await enforce("kb-search", str(auth.user.id), 60)
    result = await rag_service.search(db, auth.tenant_id, body.query, body.top_k)
    return SearchResponse(
        query=body.query,
        confidence=round(result.confidence, 4),
        confident=result.confident,
        threshold=settings.rag_min_score,
        results=[
            SearchHit(
                document_id=c.document_id,
                title=c.title,
                content=c.content,
                similarity=c.similarity,
                score=c.score,
                meta=c.meta,
            )
            for c in result.chunks
        ],
    )
