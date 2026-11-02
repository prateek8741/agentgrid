"""Human handover inbox: take over, assign, reply live, hand back to AI, close.

The live inbox (agents' browsers) is fed from domain events: every customer
message, AI reply, handover request and agent action is published on the
tenant's Redis inbox channel and pushed over WebSocket.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext
from app.channels.delivery import deliver
from app.core.errors import ConflictError, NotFoundError
from app.core.pubsub import inbox_channel, publish
from app.models import Contact, Conversation, ConversationStatus, Message, MessageRole, User
from app.services import conversation_service
from app.workers.events import Event, emit, get_event_bus

LIVE_EVENTS = {
    "message.received",
    "message.sent",
    "handover.requested",
    "conversation.updated",
    "message.human_reply",
}
HANDBACK_MESSAGE = "You're back with our virtual assistant. How else can I help?"


async def _forward_to_inbox(event: Event) -> None:
    if event.type in LIVE_EVENTS:
        await publish(
            inbox_channel(event.tenant_id),
            {
                "type": event.type,
                "conversation_id": str(event.conversation_id) if event.conversation_id else None,
                "occurred_at": event.occurred_at.isoformat(),
                **event.payload,
            },
        )


def register_inbox_subscriber() -> None:
    get_event_bus().subscribe("*", _forward_to_inbox)


async def get_conversation(db: AsyncSession, auth: AuthContext, conversation_id: uuid.UUID) -> Conversation:
    conv = (
        await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id, Conversation.tenant_id == auth.tenant_id
            )
        )
    ).scalar_one_or_none()
    if conv is None:
        raise NotFoundError("Conversation not found")
    return conv


async def list_inbox(
    db: AsyncSession,
    auth: AuthContext,
    *,
    status: ConversationStatus | None,
    mine: bool,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    stmt = (
        select(Conversation, Contact)
        .outerjoin(Contact, Contact.id == Conversation.contact_id)
        .where(Conversation.tenant_id == auth.tenant_id)
    )
    if status:
        stmt = stmt.where(Conversation.status == status)
    if mine:
        stmt = stmt.where(Conversation.assigned_user_id == auth.user.id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(stmt.order_by(Conversation.last_message_at.desc()).limit(limit).offset(offset))
    ).all()
    ids = [conv.id for conv, _ in rows]
    previews: dict[uuid.UUID, Message] = {}
    if ids:
        latest = (
            select(Message.conversation_id, func.max(Message.created_at).label("ts"))
            .where(Message.conversation_id.in_(ids), Message.tenant_id == auth.tenant_id)
            .group_by(Message.conversation_id)
            .subquery()
        )
        for msg in (
            await db.execute(
                select(Message).join(
                    latest,
                    (Message.conversation_id == latest.c.conversation_id)
                    & (Message.created_at == latest.c.ts),
                )
            )
        ).scalars():
            previews[msg.conversation_id] = msg
    counts = dict(
        (s.value, int(n))
        for s, n in (
            await db.execute(
                select(Conversation.status, func.count())
                .where(Conversation.tenant_id == auth.tenant_id)
                .group_by(Conversation.status)
            )
        ).all()
    )
    items = []
    for conv, contact in rows:
        last = previews.get(conv.id)
        items.append(
            {
                "id": conv.id,
                "channel": conv.channel.value,
                "status": conv.status.value,
                "session_id": conv.session_id,
                "assigned_user_id": conv.assigned_user_id,
                "handover_reason": conv.handover_reason,
                "handed_over_at": conv.handed_over_at,
                "last_intent": conv.last_intent,
                "sentiment": conv.sentiment,
                "message_count": conv.message_count,
                "last_message_at": conv.last_message_at,
                "contact": {
                    "id": contact.id if contact else None,
                    "name": contact.name if contact else None,
                    "phone": contact.phone if contact else None,
                    "email": contact.email if contact else None,
                },
                "last_message": {"role": last.role.value, "text": last.content[:200]} if last else None,
            }
        )
    return items, int(total), counts


async def notify_changed(conv: Conversation, auth: AuthContext, action: str) -> None:
    await emit(
        "conversation.updated",
        conv.tenant_id,
        {
            "action": action,
            "status": conv.status.value,
            "assigned_user_id": str(conv.assigned_user_id or ""),
            "by": auth.user.full_name or auth.user.email,
        },
        conversation_id=conv.id,
    )


async def take_over(
    db: AsyncSession, auth: AuthContext, conv: Conversation, *, reason: str = "agent_takeover"
) -> None:
    if conv.status is ConversationStatus.CLOSED:
        raise ConflictError("Conversation is closed")
    if conv.status is not ConversationStatus.HANDED_OVER:
        conv.status = ConversationStatus.HANDED_OVER
        conv.handover_reason = reason
        conv.handed_over_at = datetime.now(UTC)
    conv.assigned_user_id = conv.assigned_user_id or auth.user.id
    conv.dialogue_state = {}


async def assign(
    db: AsyncSession, auth: AuthContext, conv: Conversation, user_id: uuid.UUID | None
) -> Conversation:
    target = user_id or auth.user.id
    user = (
        await db.execute(
            select(User).where(User.id == target, User.tenant_id == auth.tenant_id, User.is_active.is_(True))
        )
    ).scalar_one_or_none()
    if user is None and target != auth.user.id:
        raise NotFoundError("User not found in this tenant")
    conv.assigned_user_id = target
    await db.commit()
    await notify_changed(conv, auth, "assigned")
    return conv


async def reply(db: AsyncSession, auth: AuthContext, conv: Conversation, text: str) -> Message:
    """A human agent replies; replying implicitly takes the conversation over from the AI."""
    await take_over(db, auth, conv)
    message = conversation_service.add_message(
        db,
        conv,
        MessageRole.HUMAN_AGENT,
        text,
        sender_user_id=auth.user.id,
        meta={"agent_name": auth.user.full_name or auth.user.email},
    )
    await db.commit()
    await conversation_service.append_history(conv.id, "assistant", text)
    tenant = auth.tenant
    await deliver(tenant, conv, text, sender="human", message_id=str(message.id))
    await emit(
        "message.human_reply",
        conv.tenant_id,
        {
            "message_id": str(message.id),
            "role": "human_agent",
            "text": text,
            "channel": conv.channel.value,
            "agent_name": auth.user.full_name or auth.user.email,
        },
        conversation_id=conv.id,
    )
    return message


async def hand_back(db: AsyncSession, auth: AuthContext, conv: Conversation) -> Conversation:
    if conv.status is not ConversationStatus.HANDED_OVER:
        raise ConflictError("Only handed-over conversations can be returned to the AI")
    conv.status = ConversationStatus.ACTIVE
    conv.handover_reason = None
    conv.assigned_user_id = None
    conv.dialogue_state = {}
    message = conversation_service.add_message(
        db, conv, MessageRole.ASSISTANT, HANDBACK_MESSAGE, meta={"decision": "handback"}
    )
    await db.commit()
    await conversation_service.append_history(conv.id, "assistant", HANDBACK_MESSAGE)
    await deliver(auth.tenant, conv, HANDBACK_MESSAGE, message_id=str(message.id))
    await notify_changed(conv, auth, "handed_back")
    return conv


async def close(db: AsyncSession, auth: AuthContext, conv: Conversation) -> Conversation:
    if conv.status is ConversationStatus.CLOSED:
        return conv
    if conv.status is ConversationStatus.ACTIVE and conv.handed_over_at is None:
        conv.resolved_by_ai = True  # closed without ever needing a human
    conv.status = ConversationStatus.CLOSED
    conv.closed_at = datetime.now(UTC)
    conv.dialogue_state = {}
    await db.commit()
    await conversation_service.invalidate_history(conv.id)
    await notify_changed(conv, auth, "closed")
    return conv
