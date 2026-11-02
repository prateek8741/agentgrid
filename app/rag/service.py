"""Knowledge base: ingestion and retrieval.

Ingest:  validate -> extract pages -> chunk -> batch-embed -> store (one transaction).
Search:  embed query -> vector top-k (pgvector `<=>` cosine, or in-process cosine on the
         JSON backend) -> light lexical re-rank -> confidence = best cosine similarity.

Every uploaded or deleted document bumps a per-tenant KB version in Redis so
cached FAQ answers are invalidated automatically.
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Any

import sqlalchemy as sa
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.config import settings
from app.core.errors import AppError, ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, PgVector
from app.rag import embeddings
from app.rag.chunking import chunk_pages
from app.rag.extract import ExtractionError, detect_type, extract_pages

log = get_logger(__name__)

INSERT_BATCH = 50
JSON_BACKEND_MAX_CHUNKS = 20_000
_STOP = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "do",
    "does",
    "you",
    "your",
    "what",
    "how",
    "can",
    "i",
    "we",
    "of",
    "to",
    "for",
    "in",
    "on",
    "and",
    "or",
    "it",
    "me",
    "my",
    "about",
    "please",
    "tell",
    "have",
    "with",
    "any",
}


@dataclass
class RetrievedChunk:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    content: str
    similarity: float  # cosine similarity of the embedding (confidence signal)
    score: float  # final ranking score (similarity + lexical boost)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    chunks: list[RetrievedChunk]
    confidence: float

    @property
    def confident(self) -> bool:
        return bool(self.chunks) and self.confidence >= settings.rag_min_score


async def bump_kb_version(tenant_id: uuid.UUID) -> None:
    try:
        await get_redis().incr(f"kb:ver:{tenant_id}")
    except Exception:
        log.debug("kb version bump skipped")


async def ingest_document(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    filename: str,
    content: bytes,
    title: str | None = None,
    uploaded_by_id: uuid.UUID | None = None,
) -> KnowledgeDocument:
    if not content:
        raise AppError("The file is empty")
    if len(content) > settings.upload_max_bytes:
        raise AppError(f"File exceeds the {settings.upload_max_mb} MB limit", status_code=413)
    safe_name = PurePath(filename.replace("\\", "/")).name[:300] or "document"
    try:
        mime = detect_type(safe_name, content)
    except ExtractionError as exc:
        raise AppError(str(exc), status_code=415) from exc

    digest = hashlib.sha256(content).hexdigest()
    duplicate = (
        await db.execute(
            select(KnowledgeDocument.id).where(
                KnowledgeDocument.tenant_id == tenant_id, KnowledgeDocument.sha256 == digest
            )
        )
    ).first()
    if duplicate is not None:
        raise ConflictError("This document has already been uploaded")

    try:
        pages = extract_pages(safe_name, content)
    except ExtractionError as exc:
        raise AppError(str(exc), status_code=422) from exc

    chunks = chunk_pages(pages, settings.chunk_size_chars, settings.chunk_overlap_chars)
    truncated = len(chunks) > settings.max_chunks_per_document
    chunks = chunks[: settings.max_chunks_per_document]
    if not chunks:
        raise AppError("No usable text found in the document", status_code=422)

    vectors: list[list[float]] = []
    texts = [c.text for c in chunks]
    for start in range(0, len(texts), settings.embedding_batch_size):
        vectors.extend(await embeddings.embed_documents(texts[start : start + settings.embedding_batch_size]))

    document = KnowledgeDocument(
        tenant_id=tenant_id,
        title=(title or PurePath(safe_name).stem.replace("_", " ").replace("-", " ").strip() or safe_name)[
            :300
        ],
        filename=safe_name,
        mime_type=mime,
        size_bytes=len(content),
        sha256=digest,
        chunk_count=len(chunks),
        char_count=sum(len(t) for t in texts),
        truncated=truncated,
        uploaded_by_id=uploaded_by_id,
    )
    db.add(document)
    await db.flush()

    rows = [
        {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "document_id": document.id,
            "chunk_index": i,
            "content": chunk.text,
            "meta": {k: v for k, v in chunk.meta.items() if v is not None},
            "embedding": vector,
        }
        for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True))
    ]
    for start in range(0, len(rows), INSERT_BATCH):
        await db.execute(sa.insert(KnowledgeChunk), rows[start : start + INSERT_BATCH])
    log.info("ingested document", extra={"chunks": len(rows), "truncated": truncated, "bytes": len(content)})
    return document


def _tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP and len(t) > 2}


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


async def search(
    db: AsyncSession, tenant_id: uuid.UUID, query: str, top_k: int | None = None
) -> SearchResult:
    top_k = top_k or settings.rag_top_k
    query = query.strip()[:1000]
    if not query:
        return SearchResult([], 0.0)
    has_docs = (
        await db.execute(select(KnowledgeChunk.id).where(KnowledgeChunk.tenant_id == tenant_id).limit(1))
    ).first()
    if has_docs is None:
        return SearchResult([], 0.0)

    q_vec = await embeddings.embed_query(query)
    candidates: list[tuple[KnowledgeChunk, float]] = []
    pool = top_k * 4
    if settings.uses_pgvector:
        distance = KnowledgeChunk.embedding.op("<=>", return_type=sa.Float)(
            sa.bindparam("q_vec", q_vec, type_=PgVector(settings.embedding_dim))
        )
        rows = (
            await db.execute(
                select(KnowledgeChunk, distance.label("distance"))
                .where(KnowledgeChunk.tenant_id == tenant_id)
                .order_by(distance)
                .limit(pool)
                .options(defer(KnowledgeChunk.embedding))
            )
        ).all()
        candidates = [(chunk, 1.0 - float(dist)) for chunk, dist in rows]
    else:
        rows = (
            (
                await db.execute(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.tenant_id == tenant_id)
                    .limit(JSON_BACKEND_MAX_CHUNKS)
                )
            )
            .scalars()
            .all()
        )
        scored = [(chunk, _cosine(q_vec, chunk.embedding)) for chunk in rows]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        candidates = scored[:pool]

    if not candidates:
        return SearchResult([], 0.0)
    doc_ids = {chunk.document_id for chunk, _ in candidates}
    titles = dict(
        (
            await db.execute(
                select(KnowledgeDocument.id, KnowledgeDocument.title).where(KnowledgeDocument.id.in_(doc_ids))
            )
        ).all()
    )
    q_tokens = _tokens(query)
    results: list[RetrievedChunk] = []
    for chunk, similarity in candidates:
        overlap = len(q_tokens & _tokens(chunk.content)) / len(q_tokens) if q_tokens else 0.0
        results.append(
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                title=titles.get(chunk.document_id, "Document"),
                content=chunk.content,
                similarity=round(similarity, 4),
                score=round(0.85 * similarity + 0.15 * overlap, 4),
                meta=dict(chunk.meta or {}),
            )
        )
    results.sort(key=lambda r: r.score, reverse=True)
    top = results[:top_k]
    return SearchResult(top, max(r.similarity for r in top))


async def list_documents(db: AsyncSession, tenant_id: uuid.UUID) -> list[KnowledgeDocument]:
    stmt = (
        select(KnowledgeDocument)
        .where(KnowledgeDocument.tenant_id == tenant_id)
        .order_by(KnowledgeDocument.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_document(db: AsyncSession, tenant_id: uuid.UUID, document_id: uuid.UUID) -> KnowledgeDocument:
    doc = (
        await db.execute(
            select(KnowledgeDocument).where(
                KnowledgeDocument.id == document_id, KnowledgeDocument.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise NotFoundError("Document not found")
    return doc


async def document_chunks(
    db: AsyncSession, tenant_id: uuid.UUID, document_id: uuid.UUID, limit: int = 50
) -> list[KnowledgeChunk]:
    stmt = (
        select(KnowledgeChunk)
        .where(KnowledgeChunk.tenant_id == tenant_id, KnowledgeChunk.document_id == document_id)
        .order_by(KnowledgeChunk.chunk_index)
        .limit(limit)
        .options(defer(KnowledgeChunk.embedding))
    )
    return list((await db.execute(stmt)).scalars().all())


async def delete_document(db: AsyncSession, document: KnowledgeDocument) -> None:
    await db.execute(
        delete(KnowledgeChunk)
        .where(KnowledgeChunk.document_id == document.id, KnowledgeChunk.tenant_id == document.tenant_id)
        .execution_options(synchronize_session=False)
    )
    await db.delete(document)


async def stats(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    docs = (
        await db.execute(
            select(func.count(KnowledgeDocument.id)).where(KnowledgeDocument.tenant_id == tenant_id)
        )
    ).scalar_one()
    chunks = (
        await db.execute(select(func.count(KnowledgeChunk.id)).where(KnowledgeChunk.tenant_id == tenant_id))
    ).scalar_one()
    return {"documents": int(docs), "chunks": int(chunks)}
