"""Conversations, contacts, messages and conversation memory.

Memory = the last N messages, read from a Redis list (`conv:hist:<id>`) and
rebuilt from Postgres on a cache miss. Postgres stays the source of truth.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.schema import InboundMessage
from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.models import Agent, Contact, Conversation, ConversationStatus, Message, MessageRole, Tenant

log = get_logger(__name__)
HISTORY_TTL = 24 * 3600
_HISTORY_ROLES = (MessageRole.USER, MessageRole.ASSISTANT, MessageRole.HUMAN_AGENT)


def _history_key(conversation_id: uuid.UUID) -> str:
    return f"conv:hist:{conversation_id}"


async def _find_contact(db: AsyncSession, tenant: Tenant, inbound: InboundMessage) -> Contact | None:
    info = inbound.contact
    conditions = []
    if info.phone:
        conditions.append(Contact.phone == info.phone)
    if info.email:
        conditions.append(Contact.email == info.email.lower())
    if not conditions:
        return None
    return (
        await db.execute(select(Contact).where(Contact.tenant_id == tenant.id, or_(*conditions)).limit(1))
    ).scalar_one_or_none()


async def get_or_create_conversation(
    db: AsyncSession, tenant: Tenant, agent: Agent, inbound: InboundMessage
) -> tuple[Conversation, Contact]:
    conversation = (
        await db.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant.id,
                Conversation.channel == inbound.channel,
                Conversation.session_id == inbound.session_id,
            )
        )
    ).scalar_one_or_none()

    contact: Contact | None = None
    if conversation is not None and conversation.contact_id is not None:
        contact = await db.get(Contact, conversation.contact_id)
    if contact is None:
        contact = await _find_contact(db, tenant, inbound)
    if contact is None:
        contact = Contact(
            tenant_id=tenant.id,
            channel_ids={inbound.channel.value: inbound.contact.external_id or inbound.session_id},
        )
        db.add(contact)
    info = inbound.contact
    contact.name = contact.name or info.name
    contact.phone = contact.phone or info.phone
    contact.email = contact.email or (info.email.lower() if info.email else None)
    if info.external_id and (contact.channel_ids or {}).get(inbound.channel.value) != info.external_id:
        contact.channel_ids = {**(contact.channel_ids or {}), inbound.channel.value: info.external_id}
    await db.flush()

    if conversation is None:
        meta = inbound.metadata.attribution()
        if inbound.metadata.user_agent:
            meta["user_agent"] = inbound.metadata.user_agent
        if inbound.metadata.locale:
            meta["locale"] = inbound.metadata.locale
        conversation = Conversation(
            tenant_id=tenant.id,
            agent_id=agent.id,
            contact_id=contact.id,
            channel=inbound.channel,
            session_id=inbound.session_id,
            meta=meta,
        )
        db.add(conversation)
        await db.flush()
    else:
        if conversation.contact_id is None:
            conversation.contact_id = contact.id
        if conversation.status is ConversationStatus.CLOSED:  # a new message reopens the conversation
            conversation.status = ConversationStatus.ACTIVE
            conversation.closed_at = None
            conversation.resolved_by_ai = False
            conversation.dialogue_state = {}
    return conversation, contact


def add_message(
    db: AsyncSession, conversation: Conversation, role: MessageRole, content: str, **fields: Any
) -> Message:
    message = Message(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        role=role,
        content=content,
        **fields,
    )
    db.add(message)
    conversation.message_count = (conversation.message_count or 0) + 1
    conversation.last_message_at = datetime.now(UTC)
    return message


async def recent_history(
    db: AsyncSession, conversation_id: uuid.UUID, limit: int | None = None
) -> list[dict[str, str]]:
    """Last `limit` messages as [{"role": "user"|"assistant", "content": ...}] (oldest first)."""
    limit = limit or settings.llm_history_messages
    redis = get_redis()
    key = _history_key(conversation_id)
    try:
        cached = await redis.lrange(key, -limit, -1)
        if cached:
            return [json.loads(item) for item in cached]
    except Exception as exc:
        log.warning("history cache unavailable: %s", type(exc).__name__)

    rows = (
        await db.execute(
            select(Message.role, Message.content)
            .where(Message.conversation_id == conversation_id, Message.role.in_(_HISTORY_ROLES))
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
    ).all()
    history = [
        {"role": "user" if role is MessageRole.USER else "assistant", "content": content}
        for role, content in reversed(rows)
    ]
    if history:
        try:
            pipe = redis.pipeline()
            pipe.delete(key)
            pipe.rpush(key, *[json.dumps(h) for h in history])
            pipe.expire(key, HISTORY_TTL)
            await pipe.execute()
        except Exception:
            log.debug("could not populate history cache")
    return history


async def append_history(conversation_id: uuid.UUID, role: str, content: str) -> None:
    """Append to the cached history if it exists (otherwise the next read rebuilds it from the DB)."""
    redis = get_redis()
    key = _history_key(conversation_id)
    try:
        if await redis.exists(key):
            pipe = redis.pipeline()
            pipe.rpush(key, json.dumps({"role": role, "content": content}))
            pipe.ltrim(key, -settings.llm_history_messages * 2, -1)
            pipe.expire(key, HISTORY_TTL)
            await pipe.execute()
    except Exception:
        log.debug("history cache append skipped")


async def invalidate_history(conversation_id: uuid.UUID) -> None:
    try:
        await get_redis().delete(_history_key(conversation_id))
    except Exception:
        log.debug("history cache invalidate skipped")
