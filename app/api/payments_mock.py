"""Mock payment provider pages (demo only; Razorpay test mode replaces this when keys are set).

Links are addressed by an unguessable UUID and only work for provider == "mock".
"""

from __future__ import annotations

import html
import uuid

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.api.deps import DB
from app.core.errors import ConflictError, NotFoundError
from app.models import PaymentLink, PaymentStatus, Tenant
from app.workers.events import emit

router = APIRouter(prefix="/pay/mock", tags=["payments"], include_in_schema=False)


async def _load(db: DB, link_id: uuid.UUID) -> PaymentLink:
    link = (
        await db.execute(select(PaymentLink).where(PaymentLink.id == link_id, PaymentLink.provider == "mock"))
    ).scalar_one_or_none()
    if link is None:
        raise NotFoundError("Payment link not found")
    return link


_CSS = (
    "body{font-family:system-ui,sans-serif;max-width:420px;margin:60px auto;padding:0 16px;color:#111}"
    ".card{border:1px solid #ddd;border-radius:12px;padding:24px}"
    "button{background:#4f46e5;color:#fff;border:0;border-radius:8px;padding:12px 18px;font-size:16px;"
    "cursor:pointer;width:100%}"
    ".ok{color:#047857;font-weight:600}small{color:#666}"
)


@router.get("/{link_id}", response_class=HTMLResponse)
async def mock_payment_page(link_id: uuid.UUID, db: DB) -> HTMLResponse:
    link = await _load(db, link_id)
    tenant = await db.get(Tenant, link.tenant_id)
    amount = f"{html.escape(str(link.amount))} {html.escape(link.currency)}"
    if link.status is PaymentStatus.PAID:
        action = "<p class='ok'>Payment received. Thank you!</p>"
    else:
        action = (
            f"<form method='post' action='/pay/mock/{link.id}/complete'>"
            f"<button>Pay {amount} (test)</button></form>"
        )
    body = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width'>"
        f"<title>Test payment</title><style>{_CSS}</style></head><body><div class='card'>"
        f"<h2>{html.escape(tenant.name if tenant else '')}</h2>"
        f"<p>{html.escape(link.description)}</p><h3>{amount}</h3>{action}"
        "<p><small>Mock payment provider: no real money moves.</small></p></div></body></html>"
    )
    return HTMLResponse(body)


@router.post("/{link_id}/complete", response_class=HTMLResponse)
async def mock_payment_complete(link_id: uuid.UUID, db: DB) -> HTMLResponse:
    link = await _load(db, link_id)
    if link.status is PaymentStatus.PAID:
        raise ConflictError("Already paid")
    link.status = PaymentStatus.PAID
    await db.commit()
    await emit(
        "payment.completed",
        link.tenant_id,
        {"payment_link_id": str(link.id), "amount": str(link.amount), "currency": link.currency},
        conversation_id=link.conversation_id,
    )
    return await mock_payment_page(link_id, db)
