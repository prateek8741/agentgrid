"""Kafka-compatible event bus (Redpanda in docker-compose, MSK in AWS).

Selected automatically when KAFKA_BOOTSTRAP_SERVERS is set. Events are
published to `<prefix>.events`, keyed by tenant id (per-tenant ordering).
One consumer group runs the subscribers (analytics, webhook fan-out, live
inbox, metrics), so each event is handled once across all replicas. If the
broker is unreachable, events are dispatched in-process instead of lost.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections import defaultdict

from app.core.logging import get_logger
from app.workers.events import Event, Handler, InProcessEventBus

log = get_logger(__name__)


class KafkaEventBus:
    def __init__(self, bootstrap_servers: str, topic_prefix: str) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.topic = f"{topic_prefix}.events"
        self.group_id = f"{topic_prefix}-consumers"
        self._handlers: dict[str, list[Handler]] = defaultdict(list)
        self._producer = None
        self._consumer_task: asyncio.Task[None] | None = None
        self._fallback = InProcessEventBus()

    def subscribe(self, event_type: str, handler: Handler) -> None:
        if handler not in self._handlers[event_type]:
            self._handlers[event_type].append(handler)
        self._fallback.subscribe(event_type, handler)

    async def start(self, consume: bool = True) -> None:
        from aiokafka import AIOKafkaProducer

        try:
            producer = AIOKafkaProducer(
                bootstrap_servers=self.bootstrap_servers, acks=1, linger_ms=5, request_timeout_ms=10_000
            )
            await producer.start()
            self._producer = producer
            log.info("Kafka event bus connected", extra={"topic": self.topic})
        except Exception as exc:
            log.warning("Kafka unavailable (%s): using in-process dispatch", type(exc).__name__)
            return
        if consume:
            self._consumer_task = asyncio.create_task(self._consume(), name="kafka-consumer")

    async def _dispatch(self, event: Event) -> None:
        for handler in [*self._handlers.get(event.type, []), *self._handlers.get("*", [])]:
            try:
                await handler(event)
            except Exception:
                log.exception("event handler failed", extra={"event_type": event.type})

    async def _consume(self) -> None:
        from aiokafka import AIOKafkaConsumer

        consumer = AIOKafkaConsumer(
            self.topic,
            bootstrap_servers=self.bootstrap_servers,
            group_id=self.group_id,
            auto_offset_reset="latest",
            enable_auto_commit=True,
        )
        await consumer.start()
        try:
            async for record in consumer:
                try:
                    event = Event.model_validate(json.loads(record.value))
                except (ValueError, TypeError):
                    log.warning("skipping malformed event record")
                    continue
                await self._dispatch(event)
        finally:
            await consumer.stop()

    async def publish(self, event: Event) -> None:
        if self._producer is None:
            await self._fallback.publish(event)
            return
        try:
            await self._producer.send_and_wait(
                self.topic, value=event.model_dump_json().encode(), key=str(event.tenant_id).encode()
            )
        except Exception as exc:
            log.warning("Kafka publish failed (%s): dispatching in-process", type(exc).__name__)
            await self._fallback.publish(event)

    async def drain(self) -> None:
        await self._fallback.drain()

    async def close(self) -> None:
        if self._consumer_task is not None:
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._consumer_task
        if self._producer is not None:
            with contextlib.suppress(Exception):
                await self._producer.stop()
        await self._fallback.close()
