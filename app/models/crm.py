from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import LeadStatus, LeadTemperature, str_enum


class Lead(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Built-in mini CRM lead with a status pipeline."""

    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_tenant_status", "tenant_id", "status"),
        Index("ix_leads_tenant_created", "tenant_id", "created_at"),
    )

    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), default=None
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), index=True, default=None
    )
    name: Mapped[str | None] = mapped_column(String(200), default=None)
    email: Mapped[str | None] = mapped_column(String(320), default=None)
    phone: Mapped[str | None] = mapped_column(String(40), default=None)
    need: Mapped[str | None] = mapped_column(Text, default=None)
    budget: Mapped[str | None] = mapped_column(String(120), default=None)
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), default=None)
    timeline: Mapped[str | None] = mapped_column(String(120), default=None)
    timeline_days: Mapped[int | None] = mapped_column(Integer, default=None)
    score: Mapped[int] = mapped_column(Integer, default=0)
    temperature: Mapped[LeadTemperature] = mapped_column(
        str_enum(LeadTemperature, "lead_temperature", 10), default=LeadTemperature.COLD
    )
    status: Mapped[LeadStatus] = mapped_column(str_enum(LeadStatus, "lead_status"), default=LeadStatus.NEW)
    source_channel: Mapped[str | None] = mapped_column(String(20), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    custom: Mapped[dict[str, Any]] = mapped_column(default=dict)
    external_ids: Mapped[dict[str, Any]] = mapped_column(default=dict)
