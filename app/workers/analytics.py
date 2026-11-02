"""Analytics consumer: domain events -> daily aggregates (analytics_daily).

Event-driven (no polling). Counters are upserted atomically
(INSERT ... ON CONFLICT DO UPDATE value = value + n) on Postgres and SQLite.
`rebuild_daily()` recomputes the aggregates from source tables (repair /
backfill / seed).
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models import Appointment, Conversation, Lead, Message, MessageRole
from app.models.analytics import AnalyticsDaily
from app.workers.events import Event, get_event_bus

log = get_logger(__name__)

# event type -> metrics it increments (value from payload key, or 1)
EVENT_METRICS: dict[str, list[tuple[str, str | None]]] = {
    "message.received": [("messages_in", None)],
    "message.sent": [("messages_out", None), ("latency_ms_sum", "latency_ms"), ("tokens", "tokens")],
    "message.human_reply": [("human_replies", None)],
    "handover.requested": [("handovers", None)],
    "lead.created": [("leads", None)],
    "appointment.booked": [("appointments", None)],
    "appointment.cancelled": [("appointments_cancelled", None)],
    "ticket.created": [("tickets", None)],
    "payment.completed": [("payments", None)],
}


async def increment(
    db: AsyncSession, tenant_id: uuid.UUID, day: date, channel: str, counters: dict[str, float]
) -> None:
    dialect = db.get_bind().dialect.name
    insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
    for metric, amount in counters.items():
        if not amount:
            continue
        stmt = insert_fn(AnalyticsDaily).values(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            day=day,
            channel=channel[:20],
            metric=metric,
            value=float(amount),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["tenant_id", "day", "channel", "metric"],
            set_={"value": AnalyticsDaily.value + stmt.excluded.value},
        )
        await db.execute(stmt)


def counters_for(event: Event) -> dict[str, float]:
    counters: dict[str, float] = {}
    for metric, key in EVENT_METRICS.get(event.type, []):
        value = 1.0 if key is None else float(event.payload.get(key) or 0)
        counters[metric] = counters.get(metric, 0.0) + value
    if event.type == "message.received" and event.payload.get("new_conversation"):
        counters["conversations"] = 1.0
    return counters


async def consume(event: Event) -> None:
    counters = counters_for(event)
    if not counters:
        return
    from app.core.database import SessionLocal

    channel = str(event.payload.get("channel") or "all")
    async with SessionLocal() as db:
        await increment(db, event.tenant_id, event.occurred_at.date(), channel, counters)
        await db.commit()


def register_analytics_consumer() -> None:
    get_event_bus().subscribe("*", consume)


async def rebuild_daily(db: AsyncSession, tenant_id: uuid.UUID, since: date | None = None) -> int:
    """Recompute aggregates from source tables (idempotent)."""
    since = since or (datetime.now().date() - timedelta(days=400))
    since_dt = datetime.combine(since, datetime.min.time())
    await db.execute(
        delete(AnalyticsDaily).where(AnalyticsDaily.tenant_id == tenant_id, AnalyticsDaily.day >= since)
    )
    buckets: dict[tuple[date, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))

    convs = (
        await db.execute(
            select(
                Conversation.id, Conversation.channel, Conversation.created_at, Conversation.handed_over_at
            ).where(Conversation.tenant_id == tenant_id, Conversation.created_at >= since_dt)
        )
    ).all()
    channel_of = {}
    for cid, channel, created, handed in convs:
        channel_of[cid] = channel.value
        buckets[(created.date(), channel.value)]["conversations"] += 1
        if handed is not None:
            buckets[(handed.date(), channel.value)]["handovers"] += 1

    msgs = (
        await db.execute(
            select(
                Message.conversation_id,
                Message.role,
                Message.created_at,
                Message.latency_ms,
                Message.tokens_in,
                Message.tokens_out,
            ).where(Message.tenant_id == tenant_id, Message.created_at >= since_dt)
        )
    ).all()
    for cid, role, created, latency, t_in, t_out in msgs:
        key = (created.date(), channel_of.get(cid, "all"))
        if role is MessageRole.USER:
            buckets[key]["messages_in"] += 1
        elif role is MessageRole.ASSISTANT:
            buckets[key]["messages_out"] += 1
            buckets[key]["latency_ms_sum"] += latency or 0
            buckets[key]["tokens"] += (t_in or 0) + (t_out or 0)
        elif role is MessageRole.HUMAN_AGENT:
            buckets[key]["human_replies"] += 1

    for created, channel in (
        await db.execute(
            select(Lead.created_at, Lead.source_channel).where(
                Lead.tenant_id == tenant_id, Lead.created_at >= since_dt
            )
        )
    ).all():
        buckets[(created.date(), channel or "all")]["leads"] += 1
    for created, cid in (
        await db.execute(
            select(Appointment.created_at, Appointment.conversation_id).where(
                Appointment.tenant_id == tenant_id, Appointment.created_at >= since_dt
            )
        )
    ).all():
        buckets[(created.date(), channel_of.get(cid, "all"))]["appointments"] += 1

    for (day, channel), counters in buckets.items():
        await increment(db, tenant_id, day, channel, counters)
    return len(buckets)
