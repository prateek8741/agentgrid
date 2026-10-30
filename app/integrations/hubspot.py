"""HubSpot CRM adapter (free CRM, private-app token).

Upserts a contact by email (or phone) and stores lead details as contact
properties. Skipped cleanly when HUBSPOT_PRIVATE_APP_TOKEN is not set: the
built-in CRM remains the source of truth for the demo.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.core.config import settings

BASE_URL = "https://api.hubapi.com"


class HubSpotError(RuntimeError):
    pass


def is_configured() -> bool:
    return bool(settings.hubspot_private_app_token.get_secret_value())


def _properties(lead: dict[str, Any]) -> dict[str, str]:
    name = (lead.get("name") or "").strip()
    first, _, last = name.partition(" ")
    props = {
        "email": lead.get("email") or "",
        "phone": lead.get("phone") or "",
        "firstname": first,
        "lastname": last,
        "hs_lead_status": "NEW",
        "message": " | ".join(
            f"{k}: {lead[k]}" for k in ("need", "budget", "timeline", "temperature") if lead.get(k)
        )[:1000],
    }
    return {k: v for k, v in props.items() if v}


async def upsert_contact(lead: dict[str, Any], client: httpx.AsyncClient | None = None) -> dict[str, Any]:
    if not is_configured():
        return {"skipped": True, "reason": "HUBSPOT_PRIVATE_APP_TOKEN not set"}
    if not lead.get("email"):
        return {"skipped": True, "reason": "HubSpot upsert needs an email"}
    headers = {"Authorization": f"Bearer {settings.hubspot_private_app_token.get_secret_value()}"}
    body = {"inputs": [{"idProperty": "email", "id": lead["email"], "properties": _properties(lead)}]}
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=15.0)
    try:
        resp = await client.post(
            f"{BASE_URL}/crm/v3/objects/contacts/batch/upsert", json=body, headers=headers
        )
    finally:
        if own_client:
            await client.aclose()
    if resp.status_code >= 300:
        raise HubSpotError(f"HubSpot HTTP {resp.status_code}: {resp.text[:300]}")
    results = resp.json().get("results") or []
    return {"hubspot_id": results[0].get("id") if results else None}
