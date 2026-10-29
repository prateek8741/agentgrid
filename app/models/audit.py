from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, UUIDPrimaryKeyMixin, utcnow


class AuditLog(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """Append-only record of administrative actions."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),)

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    actor_email: Mapped[str | None] = mapped_column(String(320), default=None)
    action: Mapped[str] = mapped_column(String(80), index=True)
    resource_type: Mapped[str] = mapped_column(String(60))
    resource_id: Mapped[str | None] = mapped_column(String(64), default=None)
    details: Mapped[dict[str, Any]] = mapped_column(default=dict)
    ip: Mapped[str | None] = mapped_column(String(64), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(300), default=None)
    request_id: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
