"""Human handover inbox (agents and admins)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query, WebSocket
from pydantic import BaseModel, Field

from app.api.deps import DB, AgentAuth, can_access_tenant
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.core.pubsub import inbox_channel, subscribe
from app.core.rbac import Role, role_at_least
from app.core.security import TokenError, decode_token
from app.models import ConversationStatus, Tenant, User
from app.schemas.conversation import ConversationOut, MessageOut
from app.services import inbox_service

router = APIRouter(prefix="/inbox", tags=["inbox"])
log = get_logger(__name__)


class InboxContact(BaseModel):
    id: uuid.UUID | None
    name: str | None
    phone: str | None
    email: str | None


class InboxItem(BaseModel):
    id: uuid.UUID
    channel: str
    status: str
    session_id: str
    assigned_user_id: uuid.UUID | None
    handover_reason: str | None
    handed_over_at: datetime | None
    last_intent: str | None
    sentiment: float | None
    message_count: int
    last_message_at: datetime
    contact: InboxContact
    last_message: dict[str, Any] | None


class InboxPage(BaseModel):
    items: list[InboxItem]
    total: int
    counts: dict[str, int]


class ReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class AssignIn(BaseModel):
    user_id: uuid.UUID | None = Field(default=None, description="Omit to assign to yourself")


@router.get("/conversations", response_model=InboxPage)
async def inbox(
    auth: AgentAuth,
    db: DB,
    status: ConversationStatus | None = Query(default=ConversationStatus.HANDED_OVER),
    mine: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> InboxPage:
    items, total, counts = await inbox_service.list_inbox(
        db, auth, status=status, mine=mine, limit=limit, offset=offset
    )
    return InboxPage(items=[InboxItem.model_validate(i) for i in items], total=total, counts=counts)


@router.post("/conversations/{conversation_id}/takeover", response_model=ConversationOut)
async def take_over(conversation_id: uuid.UUID, auth: AgentAuth, db: DB) -> ConversationOut:
    conv = await inbox_service.get_conversation(db, auth, conversation_id)
    await inbox_service.take_over(db, auth, conv)
    await db.commit()
    await inbox_service.notify_changed(conv, auth, "taken_over")
    return ConversationOut.model_validate(conv)


@router.post("/conversations/{conversation_id}/assign", response_model=ConversationOut)
async def assign(conversation_id: uuid.UUID, body: AssignIn, auth: AgentAuth, db: DB) -> ConversationOut:
    conv = await inbox_service.get_conversation(db, auth, conversation_id)
    return ConversationOut.model_validate(await inbox_service.assign(db, auth, conv, body.user_id))


@router.post(
    "/conversations/{conversation_id}/reply",
    response_model=MessageOut,
    summary="Reply as a human agent (delivered live on the customer's channel)",
)
async def reply(conversation_id: uuid.UUID, body: ReplyIn, auth: AgentAuth, db: DB) -> MessageOut:
    conv = await inbox_service.get_conversation(db, auth, conversation_id)
    return MessageOut.model_validate(await inbox_service.reply(db, auth, conv, body.text.strip()))


@router.post(
    "/conversations/{conversation_id}/handback",
    response_model=ConversationOut,
    summary="Return the conversation to the AI agent",
)
async def hand_back(conversation_id: uuid.UUID, auth: AgentAuth, db: DB) -> ConversationOut:
    conv = await inbox_service.get_conversation(db, auth, conversation_id)
    return ConversationOut.model_validate(await inbox_service.hand_back(db, auth, conv))


@router.post("/conversations/{conversation_id}/close", response_model=ConversationOut)
async def close(conversation_id: uuid.UUID, auth: AgentAuth, db: DB) -> ConversationOut:
    conv = await inbox_service.get_conversation(db, auth, conversation_id)
    return ConversationOut.model_validate(await inbox_service.close(db, auth, conv))


@router.websocket("/ws")
async def inbox_ws(websocket: WebSocket) -> None:
    """Live inbox feed. Browsers can't set headers on WebSockets, so the access token is a query param."""
    try:
        payload = decode_token(websocket.query_params.get("token", ""), "access")
        async with SessionLocal() as db:
            user = await db.get(User, payload.sub)
            tenant = await db.get(Tenant, payload.tid)
            allowed = (
                user is not None
                and user.is_active
                and tenant is not None
                and tenant.is_active
                and role_at_least(user.role, Role.AGENT)
                and await can_access_tenant(db, user, tenant)
            )
    except TokenError:
        allowed = False
    if not allowed:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    stop = asyncio.Event()

    async def watch_client() -> None:
        with contextlib.suppress(Exception):
            while True:
                message = await websocket.receive_json()
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        stop.set()

    watcher = asyncio.create_task(watch_client())
    try:
        async for event in subscribe(inbox_channel(payload.tid)):
            if stop.is_set():
                break
            if event.get("type") != "_idle":
                await websocket.send_json(event)
    except Exception:
        log.debug("inbox websocket closed")
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await watcher
        with contextlib.suppress(Exception):
            await websocket.close()
