"""Lead capture and CRM update tools (built-in mini CRM)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.agents import parsing
from app.agents.tools.base import ToolContext, tool
from app.models import Lead
from app.services import lead_service


class CaptureLeadArgs(BaseModel):
    name: str | None = Field(default=None, max_length=200, description="Customer's name")
    email: str | None = Field(default=None, max_length=320, description="Email address")
    phone: str | None = Field(default=None, max_length=40, description="Phone number")
    need: str | None = Field(default=None, max_length=2000, description="What the customer is looking for")
    budget: str | None = Field(default=None, max_length=120, description="Budget as stated, e.g. '80 lakh'")
    timeline: str | None = Field(default=None, max_length=120, description="When they plan to buy/start")
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("email")
    @classmethod
    def _email(cls, v: str | None) -> str | None:
        if v and not parsing.parse_email(v):
            raise ValueError("not a valid email address")
        return v

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v and not parsing.normalise_phone(v):
            raise ValueError("not a valid phone number")
        return v


def _lead_summary(lead: Lead) -> dict[str, Any]:
    return {
        "lead_id": str(lead.id),
        "score": lead.score,
        "temperature": lead.temperature.value,
        "status": lead.status.value,
        "missing": [f for f in ("name", "phone", "need", "budget", "timeline") if not getattr(lead, f)],
    }


@tool(
    "capture_lead",
    "Save or update the customer's lead details in the CRM (name, contact, need, budget, timeline). "
    "Call it whenever the customer shares any of these. Returns the lead score (hot/warm/cold).",
    CaptureLeadArgs,
)
async def capture_lead(ctx: ToolContext, args: CaptureLeadArgs) -> dict[str, Any]:
    fields = args.model_dump(exclude_none=True)
    if not fields:
        return {"error": "Provide at least one lead field"}
    lead, created = await lead_service.capture_lead(
        ctx.db, tenant_id=ctx.tenant.id, fields=fields, conversation=ctx.conversation, contact=ctx.contact
    )
    ctx.actions_enqueued = True
    ctx.emit("lead.created" if created else "lead.updated", lead_service.lead_event_payload(lead))
    return {"saved": True, "created": created, **_lead_summary(lead)}


class UpdateLeadArgs(BaseModel):
    status: Literal["new", "contacted", "qualified", "proposal", "won", "lost"] | None = None
    notes: str | None = Field(default=None, max_length=2000)
    need: str | None = Field(default=None, max_length=2000)
    budget: str | None = Field(default=None, max_length=120)
    timeline: str | None = Field(default=None, max_length=120)


@tool(
    "update_lead",
    "Update the CRM lead for this conversation (pipeline status, notes, need, budget, timeline).",
    UpdateLeadArgs,
)
async def update_lead(ctx: ToolContext, args: UpdateLeadArgs) -> dict[str, Any]:
    # The model can only touch the lead of the current conversation (created if missing).
    lead, _ = await lead_service.capture_lead(
        ctx.db, tenant_id=ctx.tenant.id, fields={}, conversation=ctx.conversation, contact=ctx.contact
    )
    changed = await lead_service.update_lead(ctx.db, lead, args.model_dump(exclude_none=True))
    ctx.emit("lead.updated", lead_service.lead_event_payload(lead))
    return {"updated": changed, **_lead_summary(lead)}
