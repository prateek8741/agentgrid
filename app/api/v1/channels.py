"""REST API channel: server-to-server integration with a tenant secret key (X-API-Key)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.orchestrator import get_orchestrator
from app.api.deps import DB, SecretKeyAuth
from app.channels.schema import AgentReply, Attachment, ChannelMetadata, ContactInfo, InboundMessage
from app.core.errors import NotFoundError
from app.models import Channel, Conversation, Message
from app.schemas.conversation import MessageOut

router = APIRouter(prefix="/channels/rest", tags=["channels"])


class RestMessageIn(BaseModel):
    session_id: str = Field(
        min_length=1, max_length=200, description="Your stable id for this end-user/thread"
    )
    text: str = Field(min_length=1, max_length=4000)
    contact: ContactInfo = Field(default_factory=ContactInfo)
    metadata: ChannelMetadata = Field(default_factory=ChannelMetadata)
    attachments: list[Attachment] = Field(default_factory=list, max_length=10)
    agent_id: uuid.UUID | None = None


@router.post("/messages", response_model=AgentReply, summary="Send a customer message, get the agent's reply")
async def rest_message(body: RestMessageIn, auth: SecretKeyAuth, db: DB) -> AgentReply:
    reply = await get_orchestrator().handle(
        db,
        InboundMessage(
            tenant_id=auth.tenant_id,
            channel=Channel.API,
            session_id=body.session_id,
            text=body.text,
            contact=body.contact,
            metadata=body.metadata,
            attachments=body.attachments,
            agent_id=body.agent_id,
        ),
    )
    reply.debug = {}  # internal diagnostics are for the console playground only
    return reply


@router.get("/conversations/{session_id}/messages", response_model=list[MessageOut])
async def rest_history(
    session_id: str, auth: SecretKeyAuth, db: DB, limit: int = Query(default=100, ge=1, le=500)
) -> list[MessageOut]:
    conversation = (
        await db.execute(
            select(Conversation).where(
                Conversation.tenant_id == auth.tenant_id,
                Conversation.channel == Channel.API,
                Conversation.session_id == session_id,
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise NotFoundError("Conversation not found")
    rows = (
        (
            await db.execute(
                select(Message)
                .where(Message.conversation_id == conversation.id, Message.tenant_id == auth.tenant_id)
                .order_by(Message.created_at)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [MessageOut.model_validate(m) for m in rows]
