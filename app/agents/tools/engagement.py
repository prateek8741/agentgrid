"""Notification and human-handover tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolContext, tool
from app.workers.workflow import enqueue


class SendNotificationArgs(BaseModel):
    recipient: Literal["team", "customer"] = Field(
        description="'team' alerts the business staff; 'customer' emails the customer (needs their email)"
    )
    subject: str = Field(min_length=1, max_length=150)
    message: str = Field(min_length=1, max_length=2000)


@tool(
    "send_notification",
    "Send an email notification to the business team or to the customer (e.g. a summary or confirmation).",
    SendNotificationArgs,
)
async def send_notification(ctx: ToolContext, args: SendNotificationArgs) -> dict[str, Any]:
    if args.recipient == "customer":
        if not ctx.contact.email:
            return {"error": "The customer's email is unknown; ask for it first"}
        enqueue(
            ctx.db,
            tenant_id=ctx.tenant.id,
            action_type="notify.email",
            payload={"to": ctx.contact.email, "subject": args.subject, "body": args.message},
            correlation_id=str(ctx.conversation.id),
        )
    else:
        enqueue(
            ctx.db,
            tenant_id=ctx.tenant.id,
            action_type="notify.team",
            payload={"subject": args.subject, "body": args.message},
            correlation_id=str(ctx.conversation.id),
        )
    ctx.actions_enqueued = True
    return {"queued": True, "recipient": args.recipient}


class RequestHandoverArgs(BaseModel):
    reason: str = Field(
        default="customer_request",
        max_length=200,
        description="Why a human is needed, e.g. 'customer asked', 'complex pricing question'",
    )


@tool(
    "request_handover",
    "Transfer the conversation to a human team member. Use when the customer asks for a person, is upset, "
    "or you cannot help confidently.",
    RequestHandoverArgs,
)
async def request_handover(ctx: ToolContext, args: RequestHandoverArgs) -> dict[str, Any]:
    ctx.handover_reason = args.reason[:60] or "agent_requested"
    return {"handover": True, "note": "Tell the customer a team member will join shortly."}
