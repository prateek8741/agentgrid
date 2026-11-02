from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    agents,
    api_keys,
    audit,
    auth,
    booking,
    commerce,
    knowledge,
    leads,
    operations,
    tenants,
    users,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(tenants.router)
api_router.include_router(users.router)
api_router.include_router(agents.templates_router)
api_router.include_router(agents.router)
api_router.include_router(knowledge.router)
api_router.include_router(leads.router)
api_router.include_router(booking.router)
api_router.include_router(commerce.router)
api_router.include_router(operations.router)
api_router.include_router(api_keys.router)
api_router.include_router(audit.router)
