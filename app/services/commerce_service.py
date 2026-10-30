"""Payment links and support tickets (agent workflow actions)."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations import payments
from app.models import Contact, Conversation, PaymentLink, Tenant, Ticket, TicketPriority
from app.services.booking_service import new_reference

MAX_PAYMENT_AMOUNT = Decimal("1000000")


async def create_payment_link(
    db: AsyncSession,
    tenant: Tenant,
    *,
    amount: Decimal,
    description: str,
    currency: str = "INR",
    conversation: Conversation | None = None,
    contact: Contact | None = None,
) -> PaymentLink:
    if amount <= 0 or amount > MAX_PAYMENT_AMOUNT:
        raise ValueError(f"Amount must be between 1 and {MAX_PAYMENT_AMOUNT}")
    link = PaymentLink(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        provider=payments.provider_name(),
        amount=amount.quantize(Decimal("0.01")),
        currency=currency.upper()[:3],
        description=description[:300],
        contact_id=contact.id if contact else None,
        conversation_id=conversation.id if conversation else None,
    )
    created = await payments.create_link(
        link_id=link.id,
        amount=link.amount,
        currency=link.currency,
        description=link.description,
        customer={
            "name": contact.name if contact else None,
            "email": contact.email if contact else None,
            "contact": contact.phone if contact else None,
        },
    )
    link.provider = created["provider"]
    link.url = created["url"]
    link.external_id = created.get("external_id")
    db.add(link)
    await db.flush()
    return link


async def create_ticket(
    db: AsyncSession,
    tenant: Tenant,
    *,
    subject: str,
    description: str,
    priority: TicketPriority = TicketPriority.NORMAL,
    conversation: Conversation | None = None,
    contact: Contact | None = None,
) -> Ticket:
    ticket = Ticket(
        tenant_id=tenant.id,
        reference=new_reference("TKT"),
        subject=subject[:300],
        description=description[:5000],
        priority=priority,
        conversation_id=conversation.id if conversation else None,
        contact_id=contact.id if contact else None,
    )
    db.add(ticket)
    await db.flush()
    return ticket
