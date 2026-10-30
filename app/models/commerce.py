from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PaymentStatus, TicketPriority, TicketStatus, str_enum


class Product(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """Per-tenant product catalog (CSV import) used for recommendations."""

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", name="uq_products_sku"),)

    sku: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str | None] = mapped_column(String(120), index=True, default=None)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    tags: Mapped[list[str]] = mapped_column(default=list)
    url: Mapped[str | None] = mapped_column(String(500), default=None)
    image_url: Mapped[str | None] = mapped_column(String(500), default=None)
    in_stock: Mapped[bool] = mapped_column(Boolean, default=True)
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)


class PaymentLink(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "payment_links"

    provider: Mapped[str] = mapped_column(String(20))  # mock | razorpay
    external_id: Mapped[str | None] = mapped_column(String(120), default=None)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    description: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[PaymentStatus] = mapped_column(
        str_enum(PaymentStatus, "payment_status"), default=PaymentStatus.CREATED
    )
    url: Mapped[str] = mapped_column(String(500), default="")
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), default=None
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), default=None
    )


class Ticket(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "tickets"
    __table_args__ = (UniqueConstraint("tenant_id", "reference", name="uq_tickets_reference"),)

    reference: Mapped[str] = mapped_column(String(20))
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), default=None
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), default=None
    )
    subject: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[TicketPriority] = mapped_column(
        str_enum(TicketPriority, "ticket_priority"), default=TicketPriority.NORMAL
    )
    status: Mapped[TicketStatus] = mapped_column(
        str_enum(TicketStatus, "ticket_status"), default=TicketStatus.OPEN
    )
    external_ref: Mapped[str | None] = mapped_column(String(120), default=None)
