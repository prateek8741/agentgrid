"""Live message fan-out over Redis pub/sub.

Used to push human-agent replies and AI replies to connected web-widget
WebSockets and to the live inbox. Works across processes/replicas with a real
Redis; with the in-process fallback it works within one process.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from app.core.logging import get_logger
from app.core.redis import get_redis

log = get_logger(__name__)


def widget_channel(tenant_id: object, session_id: str) -> str:
    return f"live:widget:{tenant_id}:{session_id}"


def inbox_channel(tenant_id: object) -> str:
    return f"live:inbox:{tenant_id}"


async def publish(channel: str, payload: dict[str, Any]) -> None:
    try:
        await get_redis().publish(channel, json.dumps(payload, default=str))
    except Exception as exc:
        log.warning("pubsub publish failed: %s", type(exc).__name__)


async def subscribe(channel: str) -> AsyncIterator[dict[str, Any]]:
    """Yield decoded messages until the consumer stops iterating."""
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(channel)
    try:
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None:
                yield {"type": "_idle"}
                continue
            try:
                yield json.loads(message["data"])
            except (TypeError, ValueError):
                continue
    finally:
        try:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()
        except Exception:
            log.debug("pubsub cleanup failed")
