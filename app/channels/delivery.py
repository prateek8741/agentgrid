"""Outbound delivery: route a reply (AI or human) to the conversation's channel."""

from __future__ import annotations

from typing import Any

from app.channels.meta import MessengerAdapter, WhatsAppAdapter
from app.channels.schema import OutboundMessage
from app.core.logging import get_logger
from app.core.pubsub import publish, widget_channel
from app.models import Channel, Conversation, Tenant
from app.workers.events import emit

log = get_logger(__name__)

ADAPTERS = {Channel.WHATSAPP: WhatsAppAdapter(), Channel.MESSENGER: MessengerAdapter()}


def with_sources(text: str, citations: list[dict[str, Any]]) -> str:
    """Plain-text channels (WhatsApp/Messenger) get the citations appended."""
    titles = list(dict.fromkeys(c.get("title") for c in citations if c.get("title")))
    if not titles:
        return text
    return f"{text}\n\nSources: " + "; ".join(f"[{i}] {t}" for i, t in enumerate(titles, start=1))


async def deliver(
    tenant: Tenant,
    conversation: Conversation,
    text: str,
    *,
    sender: str = "ai",
    quick_replies: list[str] | None = None,
    citations: list[dict[str, Any]] | None = None,
    message_id: str | None = None,
    push_to_widget: bool = True,
) -> None:
    """Deliver a reply to the customer on the conversation's channel.

    (The live inbox is fed separately from domain events, see app.services.inbox_service.)
    Web/playground replies are pushed over the widget's WebSocket channel
    (`push_to_widget=False` when the reply already went back in the HTTP
    response). API-channel integrators receive `message.outbound` webhooks.
    """
    payload = {
        "type": "message",
        "conversation_id": str(conversation.id),
        "message_id": message_id,
        "role": "human_agent" if sender == "human" else "assistant",
        "text": text,
        "quick_replies": quick_replies or [],
        "citations": citations or [],
        "status": conversation.status.value,
    }
    channel = conversation.channel
    if channel in (Channel.WEB, Channel.PLAYGROUND):
        if push_to_widget:
            await publish(widget_channel(tenant.id, conversation.session_id), payload)
    elif channel in ADAPTERS:
        outbound = OutboundMessage(
            tenant_id=tenant.id,
            channel=channel,
            session_id=conversation.session_id,
            conversation_id=conversation.id,
            account_id=(conversation.meta or {}).get("account_id"),
            text=with_sources(text, citations or []),
            quick_replies=quick_replies or [],
            sender="human" if sender == "human" else "ai",
        )
        await ADAPTERS[channel].send(tenant, outbound)
    elif channel is Channel.API and sender == "human":
        await emit("message.outbound", tenant.id, payload, conversation_id=conversation.id)
