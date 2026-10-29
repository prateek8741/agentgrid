from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class Agent(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A tenant's AI agent configuration (optionally created from a template)."""

    __tablename__ = "agents"

    name: Mapped[str] = mapped_column(String(120))
    template_key: Mapped[str | None] = mapped_column(String(60), default=None)
    persona: Mapped[str] = mapped_column(Text, default="")  # tenant-editable system prompt
    greeting: Mapped[str] = mapped_column(String(500), default="Hi! How can I help you today?")
    llm_model: Mapped[str | None] = mapped_column(String(80), default=None)  # None = platform default
    temperature: Mapped[float] = mapped_column(Float, default=0.3)
    language: Mapped[str] = mapped_column(String(10), default="en")
    enabled_tools: Mapped[list[str]] = mapped_column(default=list)
    intents: Mapped[list[Any]] = mapped_column(default=list)
    qualification_questions: Mapped[list[Any]] = mapped_column(default=list)
    flow: Mapped[dict[str, Any]] = mapped_column(default=dict)
    handover_rules: Mapped[dict[str, Any]] = mapped_column(default=dict)
    recommended_integrations: Mapped[list[str]] = mapped_column(default=list)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
