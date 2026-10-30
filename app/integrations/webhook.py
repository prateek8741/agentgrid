"""Generic signed outgoing webhooks (the 'integrate with anything' adapter).

Receivers verify `X-AgentGrid-Signature: sha256=<hmac(secret, body)>`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.core.security import sign_payload
from app.integrations.http_safety import assert_public_url


class WebhookDeliveryError(RuntimeError):
    pass


async def deliver(
    url: str, secret: str, event_type: str, data: dict[str, Any], event_id: str
) -> dict[str, Any]:
    await assert_public_url(url)
    body = json.dumps(
        {"id": event_id, "type": event_type, "data": data}, default=str, separators=(",", ":")
    ).encode()
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "AgentGrid-Webhooks/1.0",
        "X-AgentGrid-Event": event_type,
        "X-AgentGrid-Delivery": event_id,
        "X-AgentGrid-Signature": sign_payload(secret, body),
    }
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        try:
            resp = await client.post(url, content=body, headers=headers)
        except httpx.HTTPError as exc:
            raise WebhookDeliveryError(f"webhook transport error: {type(exc).__name__}") from exc
    if resp.status_code >= 300:
        raise WebhookDeliveryError(f"webhook receiver answered HTTP {resp.status_code}")
    return {"status_code": resp.status_code}
