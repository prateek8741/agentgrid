from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AppointmentStatus, str_enum


class AvailabilitySlot(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "availability_slots"
    __table_args__ = (
        Index("ix_slots_tenant_start", "tenant_id", "start_at"),
        CheckConstraint("booked_count >= 0 AND booked_count <= capacity", name="capacity"),
    )

    start_at: Mapped[datetime]
    end_at: Mapped[datetime]
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    booked_count: Mapped[int] = mapped_column(Integer, default=0)
    resource: Mapped[str | None] = mapped_column(
        String(120), default=None
    )  # e.g. "Dr. Mehta", "Site visit team"
    service: Mapped[str | None] = mapped_column(String(120), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    @property
    def available(self) -> int:
        return max(0, self.capacity - self.booked_count) if self.is_active else 0


class Appointment(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "reference", name="uq_appointments_reference"),
        Index("ix_appointments_tenant_start", "tenant_id", "start_at"),
    )

    reference: Mapped[str] = mapped_column(String(20))
    slot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("availability_slots.id", ondelete="SET NULL"), default=None
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), default=None
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), default=None
    )
    customer_name: Mapped[str] = mapped_column(String(200))
    customer_phone: Mapped[str | None] = mapped_column(String(40), index=True, default=None)
    customer_email: Mapped[str | None] = mapped_column(String(320), default=None)
    service: Mapped[str | None] = mapped_column(String(120), default=None)
    resource: Mapped[str | None] = mapped_column(String(120), default=None)
    start_at: Mapped[datetime]
    end_at: Mapped[datetime]
    status: Mapped[AppointmentStatus] = mapped_column(
        str_enum(AppointmentStatus, "appointment_status"), default=AppointmentStatus.BOOKED
    )
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(default=None)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(default=None)
