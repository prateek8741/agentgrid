"""Unified event model.

Every channel adapter (web widget, REST API, WhatsApp, Messenger, playground)
normalises its payload into `InboundMessage` before it reaches the agent, and
the agent answers with an `AgentReply` that adapters turn into
channel-specific `OutboundMessage`s. The agent never sees channel quirks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.enums import Channel


class ContactInfo(BaseModel):
    external_id: str | None = Field(default=None, max_length=200)  # wa_id, PSID, widget visitor id...
    name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=320)


class Attachment(BaseModel):
    type: Literal["image", "audio", "video", "file", "location"]
    url: str | None = Field(default=None, max_length=2000)
    mime_type: str | None = None
    name: str | None = None


class ChannelMetadata(BaseModel):
    """Channel context, including marketing attribution (UTM params)."""

    utm_source: str | None = None
    utm_medium: str | None = None
    utm_campaign: str | None = None
    utm_term: str | None = None
    utm_content: str | None = None
    page_url: str | None = Field(default=None, max_length=2000)
    referrer: str | None = Field(default=None, max_length=2000)
    user_agent: str | None = Field(default=None, max_length=500)
    locale: str | None = Field(default=None, max_length=20)
    provider_message_id: str | None = Field(default=None, max_length=200)
    extra: dict[str, Any] = Field(default_factory=dict)

    def attribution(self) -> dict[str, str]:
        return {
            k: v
            for k, v in self.model_dump(
                include={
                    "utm_source",
                    "utm_medium",
                    "utm_campaign",
                    "utm_term",
                    "utm_content",
                    "page_url",
                    "referrer",
                }
            ).items()
            if v
        }


class InboundMessage(BaseModel):
    tenant_id: uuid.UUID
    channel: Channel
    session_id: str = Field(min_length=1, max_length=200)
    contact: ContactInfo = Field(default_factory=ContactInfo)
    text: str = Field(default="", max_length=4000)
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    metadata: ChannelMetadata = Field(default_factory=ChannelMetadata)
    agent_id: uuid.UUID | None = None
    message_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    received_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Citation(BaseModel):
    document_id: str
    title: str
    snippet: str
    score: float


class AgentReply(BaseModel):
    conversation_id: uuid.UUID
    message_id: uuid.UUID | None
    text: str | None  # None when a human owns the conversation
    handed_over: bool = False
    status: str
    intent: str | None = None
    confidence: float | None = None
    quick_replies: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    latency_ms: int = 0
    debug: dict[str, Any] = Field(default_factory=dict)


class OutboundMessage(BaseModel):
    tenant_id: uuid.UUID
    channel: Channel
    session_id: str
    conversation_id: uuid.UUID | None = None  # None for system notices outside a conversation
    account_id: str | None = None  # business number / page that received the conversation
    text: str
    quick_replies: list[str] = Field(default_factory=list)
    attachments: list[Attachment] = Field(default_factory=list)
    sender: Literal["ai", "human"] = "ai"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
