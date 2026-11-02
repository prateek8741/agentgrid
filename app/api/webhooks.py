"""Meta webhooks: WhatsApp Cloud API and Facebook Messenger.

GET  = subscription verification (hub.challenge echo, constant-time token check)
POST = X-Hub-Signature-256 verified with the app secret, parsed, processed in
       the background; always answered quickly so Meta doesn't retry.
"""

from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.channels import inbound
from app.channels.meta import parse_messenger, parse_whatsapp
from app.core.config import settings
from app.core.errors import AuthError, ForbiddenError
from app.core.logging import get_logger
from app.core.security import verify_hub_signature

router = APIRouter(prefix="/webhooks", tags=["channels"])
log = get_logger(__name__)
MAX_WEBHOOK_BYTES = 1024 * 1024


def _verify_subscription(
    mode: str | None, token: str | None, challenge: str | None, expected: str
) -> PlainTextResponse:
    if mode == "subscribe" and expected and token and hmac.compare_digest(token, expected) and challenge:
        return PlainTextResponse(challenge[:200])
    raise ForbiddenError("Webhook verification failed")


async def _verified_json(request: Request, app_secret: str) -> dict:  # type: ignore[type-arg]
    body = await request.body()
    if len(body) > MAX_WEBHOOK_BYTES:
        raise AuthError("Payload too large", status_code=413)
    if not verify_hub_signature(app_secret, body, request.headers.get("x-hub-signature-256")):
        log.warning("rejected webhook with invalid signature", extra={"path": request.url.path})
        raise AuthError("Invalid webhook signature")
    try:
        return json.loads(body)
    except ValueError as exc:
        raise AuthError("Invalid JSON", status_code=400) from exc


@router.get("/whatsapp", summary="WhatsApp webhook verification")
async def whatsapp_verify(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    return _verify_subscription(mode, token, challenge, settings.whatsapp_verify_token.get_secret_value())


@router.post("/whatsapp", summary="WhatsApp Cloud API inbound messages")
async def whatsapp_inbound(request: Request) -> JSONResponse:
    payload = await _verified_json(request, settings.whatsapp_app_secret.get_secret_value())
    messages = parse_whatsapp(payload)
    for message in messages:
        inbound.spawn(inbound.handle_meta_message(message))
    return JSONResponse({"status": "ok", "received": len(messages)})


@router.get("/messenger", summary="Messenger webhook verification")
async def messenger_verify(
    mode: str | None = Query(default=None, alias="hub.mode"),
    token: str | None = Query(default=None, alias="hub.verify_token"),
    challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    return _verify_subscription(mode, token, challenge, settings.messenger_verify_token.get_secret_value())


@router.post("/messenger", summary="Facebook Messenger inbound messages")
async def messenger_inbound(request: Request) -> JSONResponse:
    payload = await _verified_json(request, settings.messenger_app_secret.get_secret_value())
    messages = parse_messenger(payload)
    for message in messages:
        inbound.spawn(inbound.handle_meta_message(message))
    return JSONResponse({"status": "ok", "received": len(messages)})
