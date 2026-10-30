"""Shared enum helpers (stored as VARCHAR + CHECK constraint: portable across Postgres/SQLite)."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum


def str_enum(enum_cls: type[StrEnum], name: str, length: int = 20) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=length,
        values_callable=lambda e: [m.value for m in e],
    )


class Channel(StrEnum):
    WEB = "web"
    API = "api"
    WHATSAPP = "whatsapp"
    MESSENGER = "messenger"
    PLAYGROUND = "playground"


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    HANDED_OVER = "handed_over"
    CLOSED = "closed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    HUMAN_AGENT = "human_agent"
    SYSTEM = "system"
    TOOL = "tool"


class LeadStatus(StrEnum):
    NEW = "new"
    CONTACTED = "contacted"
    QUALIFIED = "qualified"
    PROPOSAL = "proposal"
    WON = "won"
    LOST = "lost"


class LeadTemperature(StrEnum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"


class AppointmentStatus(StrEnum):
    BOOKED = "booked"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    NO_SHOW = "no_show"


class TicketStatus(StrEnum):
    OPEN = "open"
    PENDING = "pending"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class PaymentStatus(StrEnum):
    CREATED = "created"
    PAID = "paid"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class ActionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
