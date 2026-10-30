from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models.enums import Channel, ConversationStatus, MessageRole, str_enum


class Contact(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    """A customer known to a tenant, possibly reachable on several channels."""

    __tablename__ = "contacts"

    name: Mapped[str | None] = mapped_column(String(200), default=None)
    email: Mapped[str | None] = mapped_column(String(320), index=True, default=None)
    phone: Mapped[str | None] = mapped_column(String(40), index=True, default=None)
    channel_ids: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"whatsapp": "9198...", ...}
    attributes: Mapped[dict[str, Any]] = mapped_column(default=dict)
    external_ids: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"hubspot": "123"}


class Conversation(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", "session_id", name="uq_conversations_session"),
        Index("ix_conversations_tenant_last", "tenant_id", "last_message_at"),
        Index("ix_conversations_tenant_status", "tenant_id", "status"),
    )

    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), default=None
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"), index=True, default=None
    )
    channel: Mapped[Channel] = mapped_column(str_enum(Channel, "conversation_channel"))
    session_id: Mapped[str] = mapped_column(String(200))
    status: Mapped[ConversationStatus] = mapped_column(
        str_enum(ConversationStatus, "conversation_status"), default=ConversationStatus.ACTIVE
    )
    assigned_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    # Dialogue-manager state machine: {"flow", "step", "slots", "retries", ...}
    dialogue_state: Mapped[dict[str, Any]] = mapped_column(default=dict)
    last_intent: Mapped[str | None] = mapped_column(String(60), default=None)
    sentiment: Mapped[float | None] = mapped_column(Float, default=None)
    handover_reason: Mapped[str | None] = mapped_column(String(60), default=None)
    handed_over_at: Mapped[datetime | None] = mapped_column(default=None)
    closed_at: Mapped[datetime | None] = mapped_column(default=None)
    resolved_by_ai: Mapped[bool] = mapped_column(default=False)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)  # UTM params, page URL, user agent ...
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    first_response_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    last_message_at: Mapped[datetime] = mapped_column(default=utcnow)


class Message(UUIDPrimaryKeyMixin, TenantScopedMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_conversation_created", "conversation_id", "created_at"),)

    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[MessageRole] = mapped_column(str_enum(MessageRole, "message_role"))
    content: Mapped[str] = mapped_column(Text, default="")
    intent: Mapped[str | None] = mapped_column(String(60), default=None)
    confidence: Mapped[float | None] = mapped_column(Float, default=None)
    sentiment: Mapped[float | None] = mapped_column(Float, default=None)
    entities: Mapped[dict[str, Any]] = mapped_column(default=dict)
    tool_calls: Mapped[list[Any]] = mapped_column(default=list)
    citations: Mapped[list[Any]] = mapped_column(default=list)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    llm_provider: Mapped[str | None] = mapped_column(String(30), default=None)
    llm_model: Mapped[str | None] = mapped_column(String(80), default=None)
    attachments: Mapped[list[Any]] = mapped_column(default=list)
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict)
    sender_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
