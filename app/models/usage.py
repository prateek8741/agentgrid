from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, UUIDPrimaryKeyMixin, utcnow


class LLMUsage(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """One row per LLM call: metering for quotas, cost and analytics."""

    __tablename__ = "llm_usage"
    __table_args__ = (Index("ix_llm_usage_tenant_created", "tenant_id", "created_at"),)

    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), default=None
    )
    purpose: Mapped[str] = mapped_column(String(20))  # nlu | chat | cache
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(80))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(60), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
