"""Product recommendation, payment link and ticket tools."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agents.tools.base import ToolContext, tool
from app.models import TicketPriority
from app.services import commerce_service, product_service


class RecommendProductsArgs(BaseModel):
    query: str = Field(min_length=1, max_length=300, description="What the customer wants, in their words")
    max_price: float | None = Field(
        default=None, ge=0, description="Upper price limit if the customer gave one"
    )
    category: str | None = Field(default=None, max_length=120)


@tool(
    "recommend_products",
    "Search the store's product catalog and return matching in-stock products with prices. "
    "Only recommend products returned by this tool.",
    RecommendProductsArgs,
)
async def recommend_products(ctx: ToolContext, args: RecommendProductsArgs) -> dict[str, Any]:
    products = await product_service.search(
        ctx.db,
        ctx.tenant.id,
        query=args.query,
        max_price=Decimal(str(args.max_price)) if args.max_price is not None else None,
        category=args.category,
        limit=5,
    )
    return {
        "products": [
            {
                "sku": p.sku,
                "name": p.name,
                "price": f"{p.price} {p.currency}",
                "category": p.category,
                "url": p.url,
                "summary": (p.description or "")[:160],
            }
            for p in products
        ],
        "note": "No matching products in stock" if not products else None,
    }


class CreatePaymentLinkArgs(BaseModel):
    amount: float = Field(gt=0, le=1_000_000, description="Amount in major units (e.g. rupees)")
    description: str = Field(min_length=1, max_length=300)
    currency: Literal["INR", "USD", "EUR", "GBP"] = "INR"


@tool(
    "create_payment_link",
    "Create a secure payment link for an agreed amount and share the URL with the customer.",
    CreatePaymentLinkArgs,
)
async def create_payment_link(ctx: ToolContext, args: CreatePaymentLinkArgs) -> dict[str, Any]:
    link = await commerce_service.create_payment_link(
        ctx.db,
        ctx.tenant,
        amount=Decimal(str(args.amount)),
        description=args.description,
        currency=args.currency,
        conversation=ctx.conversation,
        contact=ctx.contact,
    )
    payload = {
        "payment_link_id": str(link.id),
        "url": link.url,
        "amount": str(link.amount),
        "currency": link.currency,
        "provider": link.provider,
    }
    ctx.emit("payment_link.created", payload)
    return payload


class CreateTicketArgs(BaseModel):
    subject: str = Field(min_length=3, max_length=300)
    description: str = Field(min_length=3, max_length=5000)
    priority: Literal["low", "normal", "high", "urgent"] = "normal"


@tool(
    "create_ticket",
    "Create a support ticket for issues that need follow-up by the team "
    "(orders, refunds, technical problems).",
    CreateTicketArgs,
)
async def create_ticket(ctx: ToolContext, args: CreateTicketArgs) -> dict[str, Any]:
    ticket = await commerce_service.create_ticket(
        ctx.db,
        ctx.tenant,
        subject=args.subject,
        description=args.description,
        priority=TicketPriority(args.priority),
        conversation=ctx.conversation,
        contact=ctx.contact,
    )
    payload = {
        "ticket_id": str(ticket.id),
        "reference": ticket.reference,
        "priority": ticket.priority.value,
        "subject": ticket.subject,
    }
    ctx.emit("ticket.created", payload)
    return {"created": True, **payload}
