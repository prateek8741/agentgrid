from __future__ import annotations

from datetime import date

from sqlalchemy import Float, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, UUIDPrimaryKeyMixin


class AnalyticsDaily(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    """Daily counters per tenant / channel / metric, written by the analytics consumer (UTC days)."""

    __tablename__ = "analytics_daily"
    __table_args__ = (
        UniqueConstraint("tenant_id", "day", "channel", "metric", name="uq_analytics_daily_key"),
    )

    day: Mapped[date]
    channel: Mapped[str] = mapped_column(String(20))
    metric: Mapped[str] = mapped_column(String(40))
    value: Mapped[float] = mapped_column(Float, default=0.0)
