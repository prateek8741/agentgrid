"""Domain event bus.

Events: message.received, message.sent, lead.created, lead.updated,
appointment.booked, appointment.cancelled, handover.requested, ticket.created,
payment_link.created, payment.completed ...

The in-process bus below is the default (lite mode). Phase 6 adds a Kafka
(Redpanda) implementation behind the same `publish`/`subscribe` interface,
selected when KAFKA_BOOTSTRAP_SERVERS is set.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.core.logging import get_logger

log = get_logger(__name__)


class Event(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: str
    tenant_id: uuid.UUID
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    conversation_id: uuid.UUID | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


Handler = Callable[[Event], Awaitable[None]]


class InProcessEventBus:
    """Fire-and-forget async dispatch; handler failures are logged, never raised."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._tasks: set[asyncio.Task[None]] = set()

    def subscribe(self, event_type: str, handler: Handler) -> None:
        """`event_type` may be "*" to receive every event."""
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)

    async def publish(self, event: Event) -> None:
        handlers = [*self._handlers.get(event.type, []), *self._handlers.get("*", [])]
        for handler in handlers:
            task = asyncio.create_task(self._run(handler, event))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _run(self, handler: Handler, event: Event) -> None:
        try:
            await handler(event)
        except Exception:
            log.exception(
                "event handler failed", extra={"event_type": event.type, "handler": handler.__qualname__}
            )

    async def drain(self) -> None:
        """Wait for in-flight handlers (tests, graceful shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def close(self) -> None:
        await self.drain()


_bus: InProcessEventBus | None = None


def get_event_bus() -> InProcessEventBus:
    global _bus
    if _bus is None:
        _bus = InProcessEventBus()
    return _bus


async def emit(
    event_type: str,
    tenant_id: uuid.UUID,
    payload: dict[str, Any] | None = None,
    conversation_id: uuid.UUID | None = None,
) -> Event:
    """Publish a domain event. Call after the related DB transaction committed."""
    event = Event(
        type=event_type, tenant_id=tenant_id, conversation_id=conversation_id, payload=payload or {}
    )
    await get_event_bus().publish(event)
    return event
