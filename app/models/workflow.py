from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models.enums import ActionStatus, str_enum


class ActionLog(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Durable record of every workflow action (email, webhook, CRM sync, reminder...).

    Doubles as the job queue for the workflow engine: pending rows whose
    `run_at` is due are claimed atomically, executed, and retried with
    exponential backoff until `max_attempts`.
    """

    __tablename__ = "action_logs"
    __table_args__ = (
        Index("ix_action_logs_due", "status", "run_at"),
        Index("ix_action_logs_tenant_created", "tenant_id", "created_at"),
    )

    action_type: Mapped[str] = mapped_column(String(60))
    status: Mapped[ActionStatus] = mapped_column(
        str_enum(ActionStatus, "action_status"), default=ActionStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(default=dict)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    run_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    correlation_id: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
