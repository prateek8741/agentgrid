"""Background processing of Meta (WhatsApp / Messenger) webhook messages.

Meta expects a fast 200 and retries otherwise, so the webhook endpoint only
verifies + parses, then hands each message to a tracked background task here.
Retried deliveries are de-duplicated by provider message id.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from sqlalchemy import select

from app.agents.orchestrator import get_orchestrator
from app.agents.parsing import normalise_phone
from app.channels.delivery import ADAPTERS, deliver
from app.channels.meta import MetaInbound
from app.channels.schema import ChannelMetadata, ContactInfo, InboundMessage, OutboundMessage
from app.core.config import settings
from app.core.logging import get_logger
from app.core.rate_limit import hit
from app.core.redis import get_redis
from app.core.tenancy import set_session_tenant
from app.models import Channel, Conversation, Tenant

log = get_logger(__name__)
SENDER_LIMIT_PER_MINUTE = 20
_tasks: set[asyncio.Task[None]] = set()
_SETTING_KEY = {Channel.WHATSAPP: "whatsapp_phone_number_id", Channel.MESSENGER: "messenger_page_id"}


def spawn(coro: Coroutine[Any, Any, None]) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def drain() -> None:
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


async def find_tenant(db: Any, channel: Channel, account_id: str) -> Tenant | None:
    key = _SETTING_KEY[channel]
    tenant = (
        await db.execute(select(Tenant).where(Tenant.settings[key].as_string() == account_id).limit(1))
    ).scalar_one_or_none()
    if tenant is None and settings.channels_default_tenant:
        tenant = (
            await db.execute(select(Tenant).where(Tenant.slug == settings.channels_default_tenant))
        ).scalar_one_or_none()
    return tenant


async def handle_meta_message(msg: MetaInbound) -> None:
    from app.core.database import SessionLocal

    try:
        async with SessionLocal() as db:
            tenant = await find_tenant(db, msg.channel, msg.account_id)
            if tenant is None or not tenant.is_active:
                log.warning("no active tenant for %s account %s", msg.channel.value, msg.account_id)
                return
            set_session_tenant(db, tenant.id)
            if msg.message_id and not await get_redis().set(
                f"dedupe:{msg.channel.value}:{msg.message_id}", "1", nx=True, ex=86400
            ):
                return  # Meta retry of a message we already processed
            if not (
                await hit("meta-sender", f"{tenant.id}:{msg.sender_id}", SENDER_LIMIT_PER_MINUTE)
            ).allowed:
                log.warning("sender rate limited on %s", msg.channel.value)
                return
            adapter = ADAPTERS[msg.channel]
            if not msg.text:
                await adapter.send(
                    tenant,
                    OutboundMessage(
                        tenant_id=tenant.id,
                        channel=msg.channel,
                        session_id=msg.sender_id,
                        account_id=msg.account_id,
                        text="Sorry, I can only read text messages for now. Please type your question.",
                    ),
                )
                return
            phone = normalise_phone("+" + msg.sender_id) if msg.channel is Channel.WHATSAPP else None
            inbound = InboundMessage(
                tenant_id=tenant.id,
                channel=msg.channel,
                session_id=msg.sender_id,
                text=msg.text[:4000],
                contact=ContactInfo(external_id=msg.sender_id, name=msg.sender_name, phone=phone),
                metadata=ChannelMetadata(
                    provider_message_id=msg.message_id or None, extra={"account_id": msg.account_id}
                ),
            )
            reply = await get_orchestrator().handle(db, inbound)
            if reply.text:
                conversation = await db.get(Conversation, reply.conversation_id)
                await deliver(
                    tenant,
                    conversation,
                    reply.text,
                    quick_replies=reply.quick_replies,
                    citations=[c.model_dump() for c in reply.citations],
                    message_id=str(reply.message_id) if reply.message_id else None,
                )
    except Exception:
        log.exception("failed to process %s message", msg.channel.value)
