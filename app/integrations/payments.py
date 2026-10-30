"""Payment-link providers: a mock provider (default) and Razorpay test mode."""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import httpx

from app.core.config import settings


class PaymentProviderError(RuntimeError):
    pass


def provider_name() -> str:
    return (
        "razorpay" if settings.razorpay_key_id and settings.razorpay_key_secret.get_secret_value() else "mock"
    )


async def create_link(
    *, link_id: uuid.UUID, amount: Decimal, currency: str, description: str, customer: dict[str, Any]
) -> dict[str, Any]:
    """Returns {"provider", "url", "external_id"}."""
    if provider_name() == "mock":
        return {
            "provider": "mock",
            "url": f"{settings.public_base_url.rstrip('/')}/pay/mock/{link_id}",
            "external_id": f"mock_{link_id.hex[:12]}",
        }
    payload: dict[str, Any] = {
        "amount": int((amount * 100).to_integral_value()),  # paise
        "currency": currency,
        "description": description[:255],
        "reference_id": str(link_id),
        "customer": {k: v for k, v in customer.items() if v and k in ("name", "email", "contact")},
        "notify": {"sms": False, "email": False},
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://api.razorpay.com/v1/payment_links",
            json=payload,
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret.get_secret_value()),
        )
    if resp.status_code >= 300:
        raise PaymentProviderError(f"Razorpay HTTP {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    return {"provider": "razorpay", "url": data.get("short_url", ""), "external_id": data.get("id")}
