from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class DocumentOut(ORMModel):
    id: uuid.UUID
    title: str
    filename: str
    mime_type: str
    size_bytes: int
    status: str
    chunk_count: int
    char_count: int
    truncated: bool
    created_at: datetime


class ChunkPreview(BaseModel):
    chunk_index: int
    content: str
    meta: dict[str, Any]


class DocumentDetail(DocumentOut):
    chunks: list[ChunkPreview] = Field(default_factory=list)


class KnowledgeOverview(BaseModel):
    documents: list[DocumentOut]
    total_documents: int
    total_chunks: int
    vector_backend: str
    embedding_model: str


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    top_k: int = Field(default=4, ge=1, le=10)


class SearchHit(BaseModel):
    document_id: uuid.UUID
    title: str
    content: str
    similarity: float
    score: float
    meta: dict[str, Any]


class SearchResponse(BaseModel):
    query: str
    confidence: float
    confident: bool
    threshold: float
    results: list[SearchHit]
