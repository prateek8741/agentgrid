"""Dashboard queries: KPIs from source tables, time series from daily aggregates."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import (
    Appointment,
    AppointmentStatus,
    Conversation,
    Lead,
    LeadStatus,
    LeadTemperature,
    LLMUsage,
    Message,
    MessageRole,
    Tenant,
)
from app.models.analytics import AnalyticsDaily
from app.services import usage_service

FIRST_RESPONSE_TARGET_MS = 2000
# Replies typed inside guided flows (names, numbers, "yes") are not reasons customers come to chat.
FLOW_REPLY_INTENTS = ("unclear", "affirm", "deny")
QUALIFIED_STATUSES = (LeadStatus.QUALIFIED, LeadStatus.PROPOSAL, LeadStatus.WON)


def _rate(part: float, whole: float) -> float:
    return round(part / whole, 4) if whole else 0.0


def _percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct * (len(ordered) - 1)))]


async def kpis(db: AsyncSession, tenant_id: uuid.UUID, since: datetime) -> dict[str, Any]:
    in_period = and_(Conversation.tenant_id == tenant_id, Conversation.created_at >= since)
    total = (await db.execute(select(func.count(Conversation.id)).where(in_period))).scalar_one()
    handed = (
        await db.execute(
            select(func.count(Conversation.id)).where(in_period, Conversation.handed_over_at.is_not(None))
        )
    ).scalar_one()
    resolved = (
        await db.execute(
            select(func.count(Conversation.id)).where(
                in_period, Conversation.handed_over_at.is_(None), Conversation.resolved_by_ai.is_(True)
            )
        )
    ).scalar_one()
    first_response = [
        v
        for (v,) in (
            await db.execute(
                select(Conversation.first_response_ms).where(
                    in_period, Conversation.first_response_ms.is_not(None)
                )
            )
        ).all()
    ]
    web_first = [
        v
        for (v,) in (
            await db.execute(
                select(Conversation.first_response_ms).where(
                    in_period, Conversation.first_response_ms.is_not(None), Conversation.channel == "web"
                )
            )
        ).all()
    ]
    avg_response = (
        await db.execute(
            select(func.avg(Message.latency_ms)).where(
                Message.tenant_id == tenant_id,
                Message.created_at >= since,
                Message.role == MessageRole.ASSISTANT,
                Message.latency_ms.is_not(None),
            )
        )
    ).scalar_one()
    avg_sentiment = (
        await db.execute(
            select(func.avg(Conversation.sentiment)).where(in_period, Conversation.sentiment.is_not(None))
        )
    ).scalar_one()

    leads_q = and_(Lead.tenant_id == tenant_id, Lead.created_at >= since)
    leads = (await db.execute(select(func.count(Lead.id)).where(leads_q))).scalar_one()
    qualified = (
        await db.execute(
            select(func.count(Lead.id)).where(
                leads_q, (Lead.status.in_(QUALIFIED_STATUSES)) | (Lead.temperature == LeadTemperature.HOT)
            )
        )
    ).scalar_one()
    won = (
        await db.execute(select(func.count(Lead.id)).where(leads_q, Lead.status == LeadStatus.WON))
    ).scalar_one()
    convs_with_lead = (
        await db.execute(
            select(func.count(distinct(Lead.conversation_id))).where(
                leads_q, Lead.conversation_id.is_not(None)
            )
        )
    ).scalar_one()

    appt_q = and_(Appointment.tenant_id == tenant_id, Appointment.created_at >= since)
    appointments = (
        await db.execute(
            select(func.count(Appointment.id)).where(
                appt_q, Appointment.status != AppointmentStatus.CANCELLED
            )
        )
    ).scalar_one()
    # Appointment conversion = conversations that booked / conversations that wanted to book.
    # Some bookings start without a booking intent (e.g. "yes" to a site-visit offer after lead
    # qualification), so the denominator is the union of both sets and the rate never exceeds 100%.
    booked_ids = set(
        (
            await db.execute(
                select(distinct(Appointment.conversation_id)).where(
                    appt_q, Appointment.conversation_id.is_not(None)
                )
            )
        ).scalars()
    )
    intent_ids = set(
        (
            await db.execute(
                select(distinct(Message.conversation_id)).where(
                    Message.tenant_id == tenant_id,
                    Message.created_at >= since,
                    Message.intent == "book_appointment",
                )
            )
        ).scalars()
    )
    booked_convs, booking_intent_convs = len(booked_ids), len(booked_ids | intent_ids)

    usage = (
        await db.execute(
            select(
                func.coalesce(func.sum(LLMUsage.prompt_tokens), 0),
                func.coalesce(func.sum(LLMUsage.completion_tokens), 0),
                func.count(LLMUsage.id),
            ).where(LLMUsage.tenant_id == tenant_id, LLMUsage.created_at >= since)
        )
    ).one()
    cached_hits = (
        await db.execute(
            select(func.count(LLMUsage.id)).where(
                LLMUsage.tenant_id == tenant_id, LLMUsage.created_at >= since, LLMUsage.cached.is_(True)
            )
        )
    ).scalar_one()
    prompt_tokens, completion_tokens, llm_calls = int(usage[0]), int(usage[1]), int(usage[2])
    cost = (
        prompt_tokens * settings.llm_cost_input_per_million
        + completion_tokens * settings.llm_cost_output_per_million
    ) / 1_000_000

    return {
        "conversations": int(total),
        "automated_resolution_rate": _rate(resolved, total),
        "escalation_rate": _rate(handed, total),
        "containment_rate": _rate(total - handed, total),
        "first_response_ms": {
            "avg": round(sum(first_response) / len(first_response)) if first_response else None,
            "p50": _percentile(first_response, 0.5),
            "p90": _percentile(first_response, 0.9),
            "web_within_target_rate": _rate(
                sum(1 for v in web_first if v < FIRST_RESPONSE_TARGET_MS), len(web_first)
            ),
            "target_ms": FIRST_RESPONSE_TARGET_MS,
        },
        "avg_response_ms": round(float(avg_response)) if avg_response is not None else None,
        "avg_sentiment": round(float(avg_sentiment), 3) if avg_sentiment is not None else None,
        "leads": int(leads),
        "lead_conversion_rate": _rate(convs_with_lead, total),
        "lead_qualification_rate": _rate(qualified, leads),
        "leads_won": int(won),
        "appointments_booked": int(appointments),
        "appointment_conversion_rate": _rate(booked_convs, booking_intent_convs),
        "tokens": {
            "prompt": prompt_tokens,
            "completion": completion_tokens,
            "total": prompt_tokens + completion_tokens,
            "llm_calls": llm_calls,
            "cache_hits": int(cached_hits),
            "estimated_cost_usd": round(cost, 4),
            "tokens_per_conversation": round((prompt_tokens + completion_tokens) / total) if total else 0,
        },
    }


async def timeseries(db: AsyncSession, tenant_id: uuid.UUID, since: date) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(
                AnalyticsDaily.day,
                AnalyticsDaily.channel,
                AnalyticsDaily.metric,
                func.sum(AnalyticsDaily.value),
            )
            .where(AnalyticsDaily.tenant_id == tenant_id, AnalyticsDaily.day >= since)
            .group_by(AnalyticsDaily.day, AnalyticsDaily.channel, AnalyticsDaily.metric)
        )
    ).all()
    days = [since + timedelta(days=i) for i in range((datetime.now(UTC).date() - since).days + 1)]
    per_day: dict[str, dict[str, float]] = {d.isoformat(): {} for d in days}
    by_channel: dict[str, float] = {}
    for day, channel, metric, value in rows:
        bucket = per_day.setdefault(day.isoformat(), {})
        bucket[metric] = bucket.get(metric, 0.0) + float(value)
        if metric == "conversations":
            by_channel[channel] = by_channel.get(channel, 0.0) + float(value)
    series = []
    for day_key in sorted(per_day):
        m = per_day[day_key]
        replies = m.get("messages_out", 0.0)
        series.append(
            {
                "day": day_key,
                "conversations": int(m.get("conversations", 0)),
                "messages": int(m.get("messages_in", 0) + replies),
                "leads": int(m.get("leads", 0)),
                "appointments": int(m.get("appointments", 0)),
                "handovers": int(m.get("handovers", 0)),
                "tokens": int(m.get("tokens", 0)),
                "avg_response_ms": round(m.get("latency_ms_sum", 0.0) / replies) if replies else None,
            }
        )
    return {"daily": series, "by_channel": {k: int(v) for k, v in sorted(by_channel.items())}}


async def breakdowns(db: AsyncSession, tenant_id: uuid.UUID, since: datetime) -> dict[str, Any]:
    intents = (
        await db.execute(
            select(Message.intent, func.count(Message.id))
            .where(
                Message.tenant_id == tenant_id,
                Message.created_at >= since,
                Message.role == MessageRole.USER,
                Message.intent.is_not(None),
                Message.intent.not_in(FLOW_REPLY_INTENTS),
            )
            .group_by(Message.intent)
            .order_by(func.count(Message.id).desc())
            .limit(10)
        )
    ).all()
    temps = (
        await db.execute(
            select(Lead.temperature, func.count(Lead.id))
            .where(Lead.tenant_id == tenant_id, Lead.created_at >= since)
            .group_by(Lead.temperature)
        )
    ).all()
    pipeline = (
        await db.execute(
            select(Lead.status, func.count(Lead.id)).where(Lead.tenant_id == tenant_id).group_by(Lead.status)
        )
    ).all()
    reasons = (
        await db.execute(
            select(Conversation.handover_reason, func.count(Conversation.id))
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.created_at >= since,
                Conversation.handover_reason.is_not(None),
            )
            .group_by(Conversation.handover_reason)
        )
    ).all()
    providers = (
        await db.execute(
            select(LLMUsage.provider, func.sum(LLMUsage.total_tokens))
            .where(LLMUsage.tenant_id == tenant_id, LLMUsage.created_at >= since)
            .group_by(LLMUsage.provider)
        )
    ).all()
    return {
        "intents": [{"intent": i, "count": int(n)} for i, n in intents],
        "lead_temperature": {t.value: int(n) for t, n in temps},
        "pipeline": {**{s.value: 0 for s in LeadStatus}, **{s.value: int(n) for s, n in pipeline}},
        "handover_reasons": {r: int(n) for r, n in reasons},
        "tokens_by_provider": {p: int(n or 0) for p, n in providers},
    }


async def overview(db: AsyncSession, tenant: Tenant, days: int) -> dict[str, Any]:
    since_day = datetime.now(UTC).date() - timedelta(days=days - 1)
    since = datetime.combine(since_day, datetime.min.time(), UTC)
    quota = await usage_service.quota_status(db, tenant)
    return {
        "tenant": {"id": str(tenant.id), "name": tenant.name},
        "period_days": days,
        "kpis": await kpis(db, tenant.id, since),
        **await timeseries(db, tenant.id, since_day),
        **await breakdowns(db, tenant.id, since),
        "quota": {
            "used": quota.used,
            "monthly_quota": quota.quota,
            "remaining": quota.remaining,
            "exceeded": quota.exceeded,
        },
    }


async def tenants_summary(db: AsyncSession, tenants: list[Tenant], days: int) -> list[dict[str, Any]]:
    """Reseller / platform view: one row per tenant."""
    since = datetime.now(UTC) - timedelta(days=days)
    out = []
    for tenant in tenants:
        k = await kpis(db, tenant.id, since)
        quota = await usage_service.quota_status(db, tenant)
        out.append(
            {
                "tenant_id": str(tenant.id),
                "name": tenant.name,
                "kind": tenant.kind.value,
                "status": tenant.status.value,
                "conversations": k["conversations"],
                "leads": k["leads"],
                "appointments": k["appointments_booked"],
                "escalation_rate": k["escalation_rate"],
                "automated_resolution_rate": k["automated_resolution_rate"],
                "tokens_period": k["tokens"]["total"],
                "tokens_month": quota.used,
                "monthly_quota": quota.quota,
                "quota_used_pct": round(100 * quota.used / quota.quota, 1) if quota.quota else None,
                "estimated_cost_usd": k["tokens"]["estimated_cost_usd"],
            }
        )
    return out
