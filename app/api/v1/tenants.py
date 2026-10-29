from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, status

from app.api.deps import DB, AdminAuth, ViewerAuth
from app.core.errors import ForbiddenError
from app.models import TenantKind
from app.schemas.common import Page
from app.schemas.tenant import Branding, TenantCreate, TenantOut, TenantSettings, TenantUpdate, TenantUsage
from app.services import tenant_service

router = APIRouter(tags=["tenants"])


# ---- current tenant ---------------------------------------------------------
@router.get("/tenant", response_model=TenantOut, summary="The active tenant")
async def current_tenant(auth: ViewerAuth) -> TenantOut:
    return TenantOut.model_validate(auth.tenant)


@router.put("/tenant/settings", response_model=TenantOut, summary="Update the active tenant's settings")
async def update_current_settings(
    body: TenantSettings, request: Request, auth: AdminAuth, db: DB
) -> TenantOut:
    tenant = await tenant_service.update_tenant(db, auth, auth.tenant, TenantUpdate(settings=body), request)
    return TenantOut.model_validate(tenant)


@router.get("/tenant/branding", response_model=Branding)
async def get_branding(auth: ViewerAuth) -> Branding:
    return Branding.model_validate(auth.tenant.branding or {})


@router.put("/tenant/branding", response_model=Branding, summary="White-label branding (console + widget)")
async def put_branding(body: Branding, request: Request, auth: AdminAuth, db: DB) -> Branding:
    return await tenant_service.update_branding(db, auth, body, request)


# ---- tenant management (super admin / agency admin) -----------------------------
@router.get("/tenants", response_model=Page[TenantOut], summary="Tenants visible to the caller")
async def list_tenants(
    auth: ViewerAuth,
    db: DB,
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[TenantOut]:
    rows, total = await tenant_service.list_accessible_tenants(db, auth, limit=limit, offset=offset, q=q)
    return Page(items=[TenantOut.model_validate(t) for t in rows], total=total, limit=limit, offset=offset)


@router.post("/tenants", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
async def create_tenant(body: TenantCreate, request: Request, auth: AdminAuth, db: DB) -> TenantOut:
    tenant = await tenant_service.create_tenant(db, auth, body, request)
    return TenantOut.model_validate(tenant)


@router.get("/tenants/{tenant_id}", response_model=TenantOut)
async def get_tenant(tenant_id: uuid.UUID, auth: ViewerAuth, db: DB) -> TenantOut:
    return TenantOut.model_validate(await tenant_service.get_accessible_tenant(db, auth, tenant_id))


@router.patch("/tenants/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: uuid.UUID, body: TenantUpdate, request: Request, auth: AdminAuth, db: DB
) -> TenantOut:
    tenant = await tenant_service.get_accessible_tenant(db, auth, tenant_id)
    return TenantOut.model_validate(await tenant_service.update_tenant(db, auth, tenant, body, request))


@router.get(
    "/agency/clients", response_model=list[TenantUsage], summary="Reseller dashboard: usage per client"
)
async def agency_clients(auth: AdminAuth, db: DB) -> list[TenantUsage]:
    home = auth.tenant if auth.tenant.id == auth.user.tenant_id else None
    if home is None or home.kind is not TenantKind.AGENCY:
        raise ForbiddenError("Switch to your agency tenant to view its clients")
    return await tenant_service.client_usage(db, home.id)
