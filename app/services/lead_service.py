"""Built-in mini CRM: lead capture, scoring, pipeline updates."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import parsing
from app.agents.scoring import LeadScore, score_lead
from app.core.errors import NotFoundError
from app.integrations import hubspot
from app.models import Contact, Conversation, Lead, LeadStatus, LeadTemperature
from app.workers.workflow import enqueue

# Max lengths match the column sizes (Postgres rejects overflow, SQLite would not).
FIELD_LIMITS = {
    "name": 200,
    "email": 320,
    "phone": 40,
    "need": 2000,
    "budget": 120,
    "timeline": 120,
    "notes": 4000,
}
LEAD_FIELDS = tuple(FIELD_LIMITS)


def _rescore(lead: Lead) -> LeadScore:
    result = score_lead(
        phone=lead.phone,
        email=lead.email,
        need=lead.need,
        budget=lead.budget,
        budget_amount=lead.budget_amount,
        timeline_days=lead.timeline_days,
        name=lead.name,
    )
    lead.score = result.score
    lead.temperature = result.temperature
    lead.custom = {**(lead.custom or {}), "score_reasons": result.reasons}
    return result


def _apply_fields(lead: Lead, fields: dict[str, Any]) -> list[str]:
    changed: list[str] = []
    for key in LEAD_FIELDS:
        value = fields.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        value = str(value).strip()
        if key == "email":
            value = parsing.parse_email(value) or value.lower()
        elif key == "phone":
            value = parsing.normalise_phone(value) or value
        if getattr(lead, key) != value:
            setattr(lead, key, value[: FIELD_LIMITS[key]])
            changed.append(key)
    if "budget" in changed or fields.get("budget_amount") is not None:
        raw_amount = fields.get("budget_amount")
        amount = (
            Decimal(str(raw_amount)) if raw_amount is not None else parsing.parse_budget(lead.budget or "")
        )
        lead.budget_amount = amount
    if "timeline" in changed or fields.get("timeline_days") is not None:
        lead.timeline_days = fields.get("timeline_days") or parsing.parse_timeline_days(lead.timeline or "")
    return changed


async def capture_lead(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    fields: dict[str, Any],
    conversation: Conversation | None = None,
    contact: Contact | None = None,
) -> tuple[Lead, bool]:
    """Create or update the lead attached to this conversation. Returns (lead, created)."""
    lead: Lead | None = None
    if conversation is not None:
        lead = (
            await db.execute(
                select(Lead)
                .where(Lead.tenant_id == tenant_id, Lead.conversation_id == conversation.id)
                .limit(1)
            )
        ).scalar_one_or_none()
    created = lead is None
    if lead is None:
        lead = Lead(
            tenant_id=tenant_id,
            conversation_id=conversation.id if conversation else None,
            contact_id=contact.id if contact else None,
            source_channel=conversation.channel.value if conversation else "manual",
        )
        db.add(lead)
    previous_temperature = None if created else lead.temperature
    _apply_fields(lead, fields)
    _rescore(lead)

    if contact is not None:  # keep the contact record in sync with what we learned
        contact.name = contact.name or lead.name
        contact.email = contact.email or lead.email
        contact.phone = contact.phone or lead.phone
    await db.flush()

    if hubspot.is_configured() and lead.email:
        enqueue(
            db,
            tenant_id=tenant_id,
            action_type="crm.hubspot_sync",
            payload={"lead_id": str(lead.id)},
            correlation_id=str(lead.id),
        )
    if lead.temperature is LeadTemperature.HOT and previous_temperature is not LeadTemperature.HOT:
        enqueue(
            db,
            tenant_id=tenant_id,
            action_type="notify.team",
            payload={
                "subject": f"🔥 Hot lead: {lead.name or 'New lead'} (score {lead.score})",
                "body": (
                    f"Name: {lead.name}\nPhone: {lead.phone}\nEmail: {lead.email}\nNeed: {lead.need}\n"
                    f"Budget: {lead.budget}\nTimeline: {lead.timeline}\nChannel: {lead.source_channel}"
                ),
            },
            correlation_id=str(lead.id),
        )
    return lead, created


def lead_event_payload(lead: Lead) -> dict[str, Any]:
    return {
        "lead_id": str(lead.id),
        "name": lead.name,
        "email": lead.email,
        "phone": lead.phone,
        "need": lead.need,
        "budget": lead.budget,
        "timeline": lead.timeline,
        "score": lead.score,
        "temperature": lead.temperature.value,
        "status": lead.status.value,
        "source_channel": lead.source_channel,
    }


async def list_leads(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    status: LeadStatus | None,
    temperature: LeadTemperature | None,
    q: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Lead], int]:
    stmt = select(Lead).where(Lead.tenant_id == tenant_id)
    if status:
        stmt = stmt.where(Lead.status == status)
    if temperature:
        stmt = stmt.where(Lead.temperature == temperature)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Lead.name.ilike(like), Lead.email.ilike(like), Lead.phone.ilike(like), Lead.need.ilike(like))
        )
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        (await db.execute(stmt.order_by(Lead.created_at.desc()).limit(limit).offset(offset))).scalars().all()
    )
    return list(rows), int(total)


async def get_lead(db: AsyncSession, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> Lead:
    lead = (
        await db.execute(select(Lead).where(Lead.id == lead_id, Lead.tenant_id == tenant_id))
    ).scalar_one_or_none()
    if lead is None:
        raise NotFoundError("Lead not found")
    return lead


async def update_lead(db: AsyncSession, lead: Lead, changes: dict[str, Any]) -> list[str]:
    """Pipeline / CRM update (used by the console and the update_lead tool)."""
    changed = _apply_fields(lead, changes)
    if changes.get("status") is not None and lead.status != LeadStatus(changes["status"]):
        lead.status = LeadStatus(changes["status"])
        changed.append("status")
    if "assigned_user_id" in changes:
        lead.assigned_user_id = changes["assigned_user_id"]
        changed.append("assigned_user_id")
    if any(k in changed for k in ("phone", "email", "need", "budget", "timeline", "name")):
        _rescore(lead)
    return changed


async def pipeline_counts(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, int]:
    rows = (
        await db.execute(
            select(Lead.status, func.count()).where(Lead.tenant_id == tenant_id).group_by(Lead.status)
        )
    ).all()
    counts = {s.value: 0 for s in LeadStatus}
    counts.update({status.value: int(n) for status, n in rows})
    return counts
