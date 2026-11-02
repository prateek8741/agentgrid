from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from sqlalchemy import select

from app.api.deps import DB, AdminAuth, SuperAdminAuth, ViewerAuth
from app.core.errors import ForbiddenError
from app.core.tenancy import unscoped
from app.models import Tenant, TenantKind
from app.services import analytics_service, audit_service
from app.workers.analytics import rebuild_daily

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/overview", summary="Dashboard: KPIs, time series, channel / intent / pipeline breakdowns")
async def overview(auth: ViewerAuth, db: DB, days: int = Query(default=30, ge=1, le=365)) -> dict[str, Any]:
    return await analytics_service.overview(db, auth.tenant, days)


@router.get("/agency", summary="Reseller dashboard: usage and KPIs per client tenant")
async def agency(
    auth: AdminAuth, db: DB, days: int = Query(default=30, ge=1, le=365)
) -> list[dict[str, Any]]:
    if auth.tenant.kind is not TenantKind.AGENCY:
        raise ForbiddenError("Switch to your agency tenant to view the reseller dashboard")
    async with unscoped(db):
        clients = list(
            (await db.execute(select(Tenant).where(Tenant.parent_id == auth.tenant_id).order_by(Tenant.name)))
            .scalars()
            .all()
        )
        return await analytics_service.tenants_summary(db, clients, days)


@router.get("/platform", summary="Platform view for super admins: every tenant")
async def platform(
    auth: SuperAdminAuth, db: DB, days: int = Query(default=30, ge=1, le=365)
) -> list[dict[str, Any]]:
    async with unscoped(db):
        tenants = list(
            (await db.execute(select(Tenant).where(Tenant.kind != TenantKind.PLATFORM).order_by(Tenant.name)))
            .scalars()
            .all()
        )
        return await analytics_service.tenants_summary(db, tenants, days)


@router.post("/rebuild", summary="Recompute daily aggregates from source data (repair / backfill)")
async def rebuild(request: Request, auth: AdminAuth, db: DB) -> dict[str, int]:
    buckets = await rebuild_daily(db, auth.tenant_id)
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="analytics.rebuild",
        resource_type="analytics",
        actor=auth.user,
        request=request,
        details={"buckets": buckets},
    )
    await db.commit()
    return {"buckets": buckets}
