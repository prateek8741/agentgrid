"""Public web-chat widget API (used by /widget.js on customers' websites).

Auth model: the public key (pk_...) identifies the tenant; each visitor gets
a server-signed session (id + HMAC token). Abuse controls: optional origin
allow-list per tenant, per-IP and per-session rate limits, message size cap.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agents.orchestrator import get_orchestrator
from app.agents.templates import render
from app.api.deps import DB, client_ip, resolve_api_key
from app.channels.delivery import deliver
from app.channels.schema import ChannelMetadata, ContactInfo, InboundMessage
from app.channels.widget_auth import new_session, verify_session
from app.core.database import SessionLocal
from app.core.errors import AppError, AuthError, ForbiddenError
from app.core.logging import get_logger
from app.core.pubsub import subscribe, widget_channel
from app.core.rate_limit import enforce, hit
from app.models import ApiKeyKind, Channel, Conversation, Message, MessageRole, Tenant
from app.schemas.tenant import Branding
from app.services import agent_service

router = APIRouter(prefix="/api/v1/widget", tags=["widget"])
log = get_logger(__name__)
IP_LIMIT, SESSION_LIMIT = 30, 20


class WidgetMessageIn(BaseModel):
    key: str = Field(max_length=100)
    session_id: str = Field(max_length=64)
    token: str = Field(max_length=64)
    text: str = Field(min_length=1, max_length=2000)
    metadata: ChannelMetadata = Field(default_factory=ChannelMetadata)
    name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=320)


def _check_origin(tenant: Tenant, origin: str | None) -> None:
    allowed = [o.rstrip("/").lower() for o in (tenant.settings or {}).get("widget_allowed_origins") or []]
    if allowed and (origin or "").rstrip("/").lower() not in allowed:
        raise ForbiddenError("This website is not allowed to use this chat widget")


async def _tenant_for(db: Any, key: str, origin: str | None) -> Tenant:
    ctx = await resolve_api_key(db, key, ApiKeyKind.PUBLIC)
    _check_origin(ctx.tenant, origin)
    return ctx.tenant


@router.get("/config", summary="Widget branding, greeting and a signed visitor session")
async def widget_config(
    request: Request,
    db: DB,
    key: str = Query(max_length=100),
    session_id: str | None = Query(default=None, max_length=64),
    token: str | None = Query(default=None, max_length=64),
) -> dict[str, Any]:
    await enforce("widget-ip", client_ip(request), IP_LIMIT * 2)
    tenant = await _tenant_for(db, key, request.headers.get("origin"))
    if not verify_session(tenant.id, session_id, token):
        session_id, token = new_session(tenant.id)
    agent = await agent_service.get_default_agent(db, tenant.id)
    branding = Branding.model_validate(tenant.branding or {})
    quick = []
    if agent and "book_appointment" in (agent.enabled_tools or []):
        quick.append(f"Book a {(agent.flow or {}).get('booking', {}).get('label', 'appointment')}")
    quick.append("Talk to a human")
    return {
        "tenant": {"name": tenant.name},
        "agent": {"name": agent.name if agent else "Assistant"},
        "greeting": render(agent.greeting, tenant.name) if agent else f"Hi! Welcome to {tenant.name}.",
        "quick_replies": quick,
        "branding": branding.model_dump(),
        "session": {"id": session_id, "token": token},
    }


async def _handle_text(
    tenant: Tenant, session_id: str, text: str, metadata: ChannelMetadata, contact: ContactInfo
) -> dict[str, Any]:
    async with SessionLocal() as db:
        reply = await get_orchestrator().handle(
            db,
            InboundMessage(
                tenant_id=tenant.id,
                channel=Channel.WEB,
                session_id=session_id,
                text=text,
                metadata=metadata,
                contact=contact,
            ),
        )
        conversation = await db.get(Conversation, reply.conversation_id)
        return {"reply": reply, "conversation": conversation}


def _public_reply(reply: Any) -> dict[str, Any]:
    return {
        "conversation_id": str(reply.conversation_id),
        "message_id": str(reply.message_id) if reply.message_id else None,
        "text": reply.text,
        "status": reply.status,
        "handed_over": reply.handed_over,
        "quick_replies": reply.quick_replies,
        "citations": [c.model_dump(include={"title", "snippet"}) for c in reply.citations],
    }


@router.post("/messages", summary="Send a visitor message (HTTP fallback when WebSocket is unavailable)")
async def widget_message(body: WidgetMessageIn, request: Request, db: DB) -> dict[str, Any]:
    await enforce("widget-ip", client_ip(request), IP_LIMIT)
    tenant = await _tenant_for(db, body.key, request.headers.get("origin"))
    if not verify_session(tenant.id, body.session_id, body.token):
        raise AuthError("Invalid widget session")
    await enforce("widget-session", f"{tenant.id}:{body.session_id}", SESSION_LIMIT)
    meta = body.metadata.model_copy(update={"user_agent": (request.headers.get("user-agent") or "")[:500]})
    result = await _handle_text(
        tenant,
        body.session_id,
        body.text,
        meta,
        ContactInfo(external_id=body.session_id, name=body.name, email=body.email),
    )
    reply = result["reply"]
    if reply.text:  # mirror to the live inbox (the visitor already gets it in this response)
        await deliver(
            tenant,
            result["conversation"],
            reply.text,
            quick_replies=reply.quick_replies,
            message_id=str(reply.message_id),
            push_to_widget=False,
        )
    return _public_reply(reply)


@router.get("/history", summary="Messages of the visitor's conversation")
async def widget_history(
    request: Request,
    db: DB,
    key: str = Query(max_length=100),
    session_id: str = Query(max_length=64),
    token: str = Query(max_length=64),
) -> dict[str, Any]:
    await enforce("widget-ip", client_ip(request), IP_LIMIT * 2)
    tenant = await _tenant_for(db, key, request.headers.get("origin"))
    if not verify_session(tenant.id, session_id, token):
        raise AuthError("Invalid widget session")
    conversation = (
        await db.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant.id,
                Conversation.channel == Channel.WEB,
                Conversation.session_id == session_id,
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        return {"status": "new", "messages": []}
    rows = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation.id,
                    Message.tenant_id == tenant.id,
                    Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT, MessageRole.HUMAN_AGENT]),
                )
                .order_by(Message.created_at.desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    return {
        "status": conversation.status.value,
        "messages": [
            {"role": m.role.value, "text": m.content, "created_at": m.created_at.isoformat()}
            for m in reversed(rows)
        ],
    }


@router.websocket("/ws")
async def widget_ws(websocket: WebSocket) -> None:
    """Live chat: visitor messages in, AI + human-agent replies out (via Redis pub/sub)."""
    params = websocket.query_params
    key, session_id, token = params.get("key", ""), params.get("session_id", ""), params.get("token", "")
    try:
        async with SessionLocal() as db:
            tenant = await _tenant_for(db, key, websocket.headers.get("origin"))
        if not verify_session(tenant.id, session_id, token):
            raise AuthError("Invalid widget session")
    except AppError:
        await websocket.close(code=4401)
        return
    await websocket.accept()
    ip = websocket.client.host if websocket.client else "unknown"
    stop = asyncio.Event()

    async def forward_live() -> None:
        async for event in subscribe(widget_channel(tenant.id, session_id)):
            if stop.is_set():
                return
            if event.get("type") != "_idle":
                await websocket.send_json(event)

    async def receive_loop() -> None:
        try:
            while True:
                data = await websocket.receive_json()
                if data.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                text = str(data.get("text") or "").strip()[:2000]
                if data.get("type") != "message" or not text:
                    continue
                allowed = (await hit("widget-ip", ip, IP_LIMIT)).allowed and (
                    await hit("widget-session", f"{tenant.id}:{session_id}", SESSION_LIMIT)
                ).allowed
                if not allowed:
                    await websocket.send_json(
                        {"type": "error", "message": "You're sending messages too fast."}
                    )
                    continue
                await websocket.send_json({"type": "typing"})
                try:
                    meta = ChannelMetadata.model_validate(data.get("metadata") or {})
                except ValueError:
                    meta = ChannelMetadata()
                result = await _handle_text(
                    tenant, session_id, text, meta, ContactInfo(external_id=session_id)
                )
                reply = result["reply"]
                if reply.text:  # goes out through pub/sub, so every open tab of this visitor sees it
                    await deliver(
                        tenant,
                        result["conversation"],
                        reply.text,
                        quick_replies=reply.quick_replies,
                        citations=[c.model_dump() for c in reply.citations],
                        message_id=str(reply.message_id),
                    )
                else:
                    await websocket.send_json({"type": "status", "status": reply.status})
        except WebSocketDisconnect:
            return
        finally:
            stop.set()

    forwarder = asyncio.create_task(forward_live())
    try:
        await receive_loop()
    except Exception:
        log.exception("widget websocket error")
    finally:
        forwarder.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await forwarder
        with contextlib.suppress(Exception):
            await websocket.close()
