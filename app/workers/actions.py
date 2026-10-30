"""Built-in workflow actions and the event -> outgoing-webhook fan-out."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.core.tenancy import set_session_tenant
from app.integrations import hubspot
from app.integrations.email import send_email
from app.integrations.http_safety import UnsafeURLError
from app.integrations.webhook import deliver
from app.models import Appointment, AppointmentStatus, Lead, Tenant
from app.workers.events import Event, get_event_bus
from app.workers.workflow import (
    ActionContext,
    PermanentActionError,
    action_handler,
    enqueue,
    get_workflow_engine,
)

log = get_logger(__name__)

# Domain events forwarded to a tenant's outgoing webhook, if one is configured.
WEBHOOK_EVENTS = {
    "lead.created",
    "lead.updated",
    "appointment.booked",
    "appointment.cancelled",
    "handover.requested",
    "ticket.created",
    "payment_link.created",
    "payment.completed",
}


def _webhook_secret(tenant: Tenant) -> str:
    """Per-tenant signing secret; generated and persisted on first use."""
    current = dict(tenant.settings or {})
    secret = current.get("outgoing_webhook_secret")
    if not secret:
        secret = "whsec_" + secrets.token_urlsafe(24)
        current["outgoing_webhook_secret"] = secret
        tenant.settings = current
    return str(secret)


@action_handler("notify.email")
async def _notify_email(ctx: ActionContext) -> dict[str, Any]:
    to = ctx.payload.get("to")
    if not to:
        raise PermanentActionError("notify.email requires 'to'")
    return await send_email(to, str(ctx.payload.get("subject", ""))[:200], str(ctx.payload.get("body", "")))


@action_handler("notify.team")
async def _notify_team(ctx: ActionContext) -> dict[str, Any]:
    """Internal alert to the tenant's team (email if configured, otherwise logged)."""
    to = (ctx.tenant.settings or {}).get("notification_email")
    subject = str(ctx.payload.get("subject", "AgentGrid notification"))[:200]
    body = str(ctx.payload.get("body", ""))
    if not to:
        log.info("team notification (no notification_email set): %s", subject)
        return {"delivered": False, "mode": "logged"}
    return await send_email(to, subject, body)


@action_handler("webhook.deliver")
async def _webhook_deliver(ctx: ActionContext) -> dict[str, Any]:
    url = (ctx.tenant.settings or {}).get("outgoing_webhook_url")
    if not url:
        return {"skipped": True, "reason": "no outgoing_webhook_url configured"}
    secret = _webhook_secret(ctx.tenant)
    try:
        return await deliver(
            url,
            secret,
            str(ctx.payload["event_type"]),
            ctx.payload.get("data", {}),
            str(ctx.payload.get("event_id") or ctx.action.id),
        )
    except UnsafeURLError as exc:
        raise PermanentActionError(str(exc)) from exc


@action_handler("crm.hubspot_sync")
async def _hubspot_sync(ctx: ActionContext) -> dict[str, Any]:
    lead = await ctx.db.get(Lead, uuid.UUID(str(ctx.payload["lead_id"])))
    if lead is None:
        raise PermanentActionError("lead not found")
    result = await hubspot.upsert_contact(
        {
            "name": lead.name,
            "email": lead.email,
            "phone": lead.phone,
            "need": lead.need,
            "budget": lead.budget,
            "timeline": lead.timeline,
            "temperature": lead.temperature.value,
        }
    )
    if result.get("hubspot_id"):
        lead.external_ids = {**(lead.external_ids or {}), "hubspot": result["hubspot_id"]}
    return result


@action_handler("appointment.reminder")
async def _appointment_reminder(ctx: ActionContext) -> dict[str, Any]:
    appt = await ctx.db.get(Appointment, uuid.UUID(str(ctx.payload["appointment_id"])))
    if appt is None or appt.status is not AppointmentStatus.BOOKED:
        return {"skipped": True, "reason": "appointment no longer booked"}
    if appt.reminder_sent_at is not None:
        return {"skipped": True, "reason": "reminder already sent"}
    when = appt.start_at.strftime("%d %b %Y, %H:%M UTC")
    body = (
        f"Hi {appt.customer_name}, this is a reminder of your {appt.service or 'appointment'} with "
        f"{ctx.tenant.name} on {when}. Reference: {appt.reference}."
    )
    result: dict[str, Any] = {"channel": "logged"}
    if appt.customer_email:
        result = await send_email(appt.customer_email, f"Reminder: your appointment ({appt.reference})", body)
    else:
        log.info("appointment reminder (no email on file) ref=%s", appt.reference)
    appt.reminder_sent_at = datetime.now(UTC)
    return result


async def _forward_to_webhook(event: Event) -> None:
    """Event-driven fan-out: enqueue a durable, retried webhook delivery."""
    if event.type not in WEBHOOK_EVENTS:
        return
    from app.core.database import SessionLocal

    async with SessionLocal() as db:
        tenant = await db.get(Tenant, event.tenant_id)
        if tenant is None or not (tenant.settings or {}).get("outgoing_webhook_url"):
            return
        set_session_tenant(db, tenant.id)
        enqueue(
            db,
            tenant_id=tenant.id,
            action_type="webhook.deliver",
            payload={"event_type": event.type, "event_id": event.id, "data": event.payload},
            correlation_id=event.id,
        )
        await db.commit()
    get_workflow_engine().wake()


def register_subscribers() -> None:
    get_event_bus().subscribe("*", _forward_to_webhook)
