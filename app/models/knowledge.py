"""Knowledge base: per-tenant documents and their embedded chunks."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from app.core.config import settings
from app.models.base import Base, JSONType, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class PgVector(UserDefinedType[list[float]]):
    """pgvector column bound as text and cast server-side.

    Sending '[0.1,0.2,...]' as TEXT and casting with `::vector` works with any
    driver (no asyncpg codec registration, no numpy coupling).
    """

    cache_ok = True

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def get_col_spec(self, **_kw: Any) -> str:
        return f"vector({self.dim})"

    def bind_processor(self, dialect: Any) -> Any:
        def process(value: Any) -> str | None:
            if value is None:
                return None
            return "[" + ",".join(f"{float(v):.6g}" for v in value) + "]"

        return process

    def bind_expression(self, bindvalue: Any) -> Any:
        # Renders CAST(CAST(:param AS TEXT) AS vector(N)); the driver only ever sees text.
        return sa.cast(sa.cast(bindvalue, sa.Text), self)

    def result_processor(self, dialect: Any, coltype: Any) -> Any:
        def process(value: Any) -> list[float] | None:
            if value is None or isinstance(value, list):
                return value
            return [float(x) for x in str(value).strip("[]").split(",") if x]

        return process


def embedding_column_type() -> sa.types.TypeEngine[Any]:
    return PgVector(settings.embedding_dim) if settings.uses_pgvector else JSONType


class KnowledgeDocument(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_documents"
    __table_args__ = (UniqueConstraint("tenant_id", "sha256", name="uq_knowledge_documents_sha"),)

    title: Mapped[str] = mapped_column(String(300))
    filename: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="ready")  # ready | failed
    error: Mapped[str | None] = mapped_column(Text, default=None)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(default=False)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )


class KnowledgeChunk(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (Index("ix_knowledge_chunks_document", "document_id", "chunk_index"),)

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    embedding: Mapped[list[float]] = mapped_column(embedding_column_type())
