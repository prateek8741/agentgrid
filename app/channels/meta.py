"""WhatsApp Cloud API and Facebook Messenger adapters (Meta Graph API).

Inbound: webhook payloads are verified (X-Hub-Signature-256) by the API layer
and parsed here into channel-neutral messages. Outbound: replies are sent via
the Graph API. Without access tokens the reply is logged instead, so the demo
works end-to-end in tests and locally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.channels.schema import OutboundMessage
from app.core.config import settings
from app.core.logging import get_logger
from app.models import Channel, Tenant

log = get_logger(__name__)


class ChannelDeliveryError(RuntimeError):
    pass


@dataclass
class MetaInbound:
    channel: Channel
    account_id: str  # WhatsApp phone_number_id or Messenger page id
    sender_id: str  # wa_id or PSID
    text: str
    message_id: str
    sender_name: str | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)


def _graph(path: str) -> str:
    return f"https://graph.facebook.com/{settings.graph_api_version}/{path.lstrip('/')}"


async def _post(
    url: str, json_body: dict[str, Any], *, token: str | None = None, params: dict[str, str] | None = None
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(url, json=json_body, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise ChannelDeliveryError(f"Graph API transport error: {type(exc).__name__}") from exc
    if resp.status_code >= 300:
        raise ChannelDeliveryError(f"Graph API HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


# ------------------------------------------------------------------ WhatsApp
def parse_whatsapp(payload: dict[str, Any]) -> list[MetaInbound]:
    out: list[MetaInbound] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            account = str((value.get("metadata") or {}).get("phone_number_id", ""))
            names = {
                c.get("wa_id"): (c.get("profile") or {}).get("name") for c in value.get("contacts") or []
            }
            for msg in value.get("messages") or []:  # delivery "statuses" are ignored
                kind = msg.get("type")
                text, attachments = "", []
                if kind == "text":
                    text = (msg.get("text") or {}).get("body", "")
                elif kind == "interactive":
                    inter = msg.get("interactive") or {}
                    text = (inter.get("button_reply") or inter.get("list_reply") or {}).get("title", "")
                elif kind == "button":
                    text = (msg.get("button") or {}).get("text", "")
                elif kind in ("image", "audio", "video", "document", "location", "sticker"):
                    attachments.append({"type": "file" if kind in ("document", "sticker") else kind})
                sender = str(msg.get("from", ""))
                out.append(
                    MetaInbound(
                        Channel.WHATSAPP,
                        account,
                        sender,
                        text.strip(),
                        str(msg.get("id", "")),
                        names.get(sender),
                        attachments,
                    )
                )
    return out


class WhatsAppAdapter:
    channel = Channel.WHATSAPP

    async def send(self, tenant: Tenant, message: OutboundMessage) -> None:
        # Reply from the number that received the conversation, else the tenant's / platform's number.
        phone_number_id = (
            message.account_id
            or (tenant.settings or {}).get("whatsapp_phone_number_id")
            or settings.whatsapp_phone_number_id
        )
        token = settings.whatsapp_access_token.get_secret_value()
        if not phone_number_id or not token:
            log.info("whatsapp reply (not configured, logged only) to=%s", message.session_id)
            return
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": message.session_id,
        }
        buttons = [q for q in message.quick_replies if len(q) <= 20][:3]
        if buttons and len(message.text) <= 1024:
            body["type"] = "interactive"
            body["interactive"] = {
                "type": "button",
                "body": {"text": message.text},
                "action": {
                    "buttons": [
                        {"type": "reply", "reply": {"id": f"qr{i}", "title": q}}
                        for i, q in enumerate(buttons)
                    ]
                },
            }
        else:
            body["type"] = "text"
            body["text"] = {"preview_url": False, "body": message.text[:4096]}
        await _post(_graph(f"{phone_number_id}/messages"), body, token=token)


# ------------------------------------------------------------------ Messenger
def parse_messenger(payload: dict[str, Any]) -> list[MetaInbound]:
    out: list[MetaInbound] = []
    if payload.get("object") != "page":
        return out
    for entry in payload.get("entry") or []:
        page_id = str(entry.get("id", ""))
        for event in entry.get("messaging") or []:
            sender = str((event.get("sender") or {}).get("id", ""))
            message = event.get("message") or {}
            if message.get("is_echo"):
                continue  # our own outgoing message
            if message:
                text = (message.get("quick_reply") or {}).get("payload") or message.get("text") or ""
                attachments = [{"type": a.get("type", "file")} for a in message.get("attachments") or []]
                out.append(
                    MetaInbound(
                        Channel.MESSENGER,
                        page_id,
                        sender,
                        text.strip(),
                        str(message.get("mid", "")),
                        attachments=attachments,
                    )
                )
            elif event.get("postback"):
                postback = event["postback"]
                out.append(
                    MetaInbound(
                        Channel.MESSENGER,
                        page_id,
                        sender,
                        str(postback.get("title") or postback.get("payload") or "").strip(),
                        str(postback.get("mid") or f"pb-{event.get('timestamp', '')}"),
                    )
                )
    return out


class MessengerAdapter:
    channel = Channel.MESSENGER

    async def send(self, tenant: Tenant, message: OutboundMessage) -> None:
        token = settings.messenger_page_access_token.get_secret_value()
        if not token:
            log.info("messenger reply (not configured, logged only) to=%s", message.session_id)
            return
        msg: dict[str, Any] = {"text": message.text[:2000]}
        replies = [q for q in message.quick_replies if q][:13]
        if replies:
            msg["quick_replies"] = [
                {"content_type": "text", "title": q[:20], "payload": q[:1000]} for q in replies
            ]
        body = {"recipient": {"id": message.session_id}, "messaging_type": "RESPONSE", "message": msg}
        await _post(_graph("me/messages"), body, token=token)
