from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models import Channel, ConversationStatus, MessageRole
from app.schemas.common import ORMModel


class ConversationOut(ORMModel):
    id: uuid.UUID
    agent_id: uuid.UUID | None
    contact_id: uuid.UUID | None
    channel: Channel
    session_id: str
    status: ConversationStatus
    assigned_user_id: uuid.UUID | None
    last_intent: str | None
    sentiment: float | None
    handover_reason: str | None
    resolved_by_ai: bool
    meta: dict[str, Any]
    message_count: int
    first_response_ms: int | None
    last_message_at: datetime
    created_at: datetime


class MessageOut(ORMModel):
    id: uuid.UUID
    role: MessageRole
    content: str
    intent: str | None
    confidence: float | None
    sentiment: float | None
    tool_calls: list[Any]
    citations: list[Any]
    tokens_in: int
    tokens_out: int
    latency_ms: int | None
    llm_provider: str | None
    sender_user_id: uuid.UUID | None
    created_at: datetime


class PlaygroundRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    session_id: str | None = Field(
        default=None, max_length=100, description="Reuse to continue a conversation"
    )


class ActionLogOut(ORMModel):
    id: uuid.UUID
    action_type: str
    status: str
    attempts: int
    max_attempts: int
    payload: dict[str, Any]
    result: dict[str, Any]
    last_error: str | None
    run_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    correlation_id: str | None
    created_at: datetime


class UsageOut(BaseModel):
    month: str
    tokens_used: int
    monthly_quota: int
    remaining: int | None
    exceeded: bool
    llm_providers: list[str]
