"""Tenant provisioning and the agency -> client hierarchy."""

from __future__ import annotations

import re
import uuid

from fastapi import Request
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, can_access_tenant
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.rbac import Role
from app.core.security import generate_api_key, hash_password
from app.core.tenancy import unscoped
from app.models import Agent, ApiKey, ApiKeyKind, Tenant, TenantKind, TenantStatus, User
from app.schemas.tenant import Branding, TenantCreate, TenantSettings, TenantUpdate, TenantUsage
from app.services import audit_service

DEFAULT_PERSONA = (
    "You are a friendly, concise customer assistant for {business}. Answer questions using the "
    "business knowledge base, capture leads and book appointments when relevant, and hand over "
    "to a human whenever the customer asks or you are unsure."
)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:60] or "tenant"


async def _unique_slug(db: AsyncSession, base: str) -> str:
    candidate, n = base, 1
    while (await db.execute(select(Tenant.id).where(Tenant.slug == candidate))).first() is not None:
        n += 1
        candidate = f"{base}-{n}"
    return candidate


async def provision_tenant(
    db: AsyncSession,
    *,
    name: str,
    kind: TenantKind,
    parent_id: uuid.UUID | None = None,
    slug: str | None = None,
    industry: str | None = None,
    plan: str = "free",
    tenant_settings: TenantSettings | None = None,
    branding: Branding | None = None,
) -> Tenant:
    """Create a tenant with sane defaults: a default agent and a public widget key.

    Caller controls the transaction (and must use an unscoped session).
    """
    if slug:
        if (await db.execute(select(Tenant.id).where(Tenant.slug == slug))).first() is not None:
            raise ConflictError(f"Slug '{slug}' is already taken")
    else:
        slug = await _unique_slug(db, slugify(name))

    tenant = Tenant(
        name=name,
        slug=slug,
        kind=kind,
        parent_id=parent_id,
        industry=industry,
        plan=plan,
        settings=(tenant_settings or TenantSettings()).model_dump(mode="json"),
        branding=(branding or Branding(brand_name=name)).model_dump(mode="json"),
    )
    db.add(tenant)
    await db.flush()

    if kind is not TenantKind.PLATFORM:
        db.add(
            Agent(
                tenant_id=tenant.id,
                name="Assistant",
                persona=DEFAULT_PERSONA.format(business=name),
                greeting=f"Hi! Welcome to {name}. How can I help you today?",
                enabled_tools=["knowledge_search", "capture_lead", "request_handover"],
                is_default=True,
            )
        )
        raw, prefix, key_hash = generate_api_key("public")
        db.add(
            ApiKey(
                tenant_id=tenant.id,
                name="Website widget",
                kind=ApiKeyKind.PUBLIC,
                prefix=prefix,
                key_hash=key_hash,
                public_key=raw,
            )
        )
    return tenant


async def create_tenant(
    db: AsyncSession, auth: AuthContext, data: TenantCreate, request: Request | None
) -> Tenant:
    kind = TenantKind(data.kind)
    parent_id = data.parent_id

    if auth.role is Role.SUPER_ADMIN:
        pass
    elif auth.role is Role.TENANT_ADMIN and auth.tenant.kind is TenantKind.AGENCY:
        # Agency admins may only create business clients under their own agency.
        if kind is not TenantKind.BUSINESS:
            raise ForbiddenError("Agencies can only create business client tenants")
        if parent_id not in (None, auth.tenant_id):
            raise ForbiddenError("Client tenants must belong to your agency")
        parent_id = auth.tenant_id
    else:
        raise ForbiddenError("Only super admins and agency admins can create tenants")

    async with unscoped(db):
        if parent_id is not None:
            parent = await db.get(Tenant, parent_id)
            if parent is None or parent.kind is not TenantKind.AGENCY:
                raise ConflictError("Parent tenant must be an agency")
            if kind is TenantKind.AGENCY:
                raise ConflictError("Agencies cannot be nested")
        if data.admin is not None:
            exists = (await db.execute(select(User.id).where(User.email == data.admin.email.lower()))).first()
            if exists is not None:
                raise ConflictError("A user with this email already exists")

        tenant = await provision_tenant(
            db,
            name=data.name,
            kind=kind,
            parent_id=parent_id,
            slug=data.slug,
            industry=data.industry,
            plan=data.plan,
            tenant_settings=data.settings,
            branding=data.branding,
        )
        if data.admin is not None:
            db.add(
                User(
                    tenant_id=tenant.id,
                    email=data.admin.email.lower(),
                    full_name=data.admin.full_name,
                    password_hash=await hash_password(data.admin.password),
                    role=Role.TENANT_ADMIN,
                )
            )
        audit_service.record(
            db,
            tenant_id=auth.tenant_id,
            action="tenant.create",
            resource_type="tenant",
            resource_id=tenant.id,
            actor=auth.user,
            request=request,
            details={
                "name": tenant.name,
                "kind": kind.value,
                "parent_id": str(parent_id) if parent_id else None,
            },
        )
        await db.commit()
        await db.refresh(tenant)
    return tenant


async def list_accessible_tenants(
    db: AsyncSession, auth: AuthContext, *, limit: int, offset: int, q: str | None = None
) -> tuple[list[Tenant], int]:
    """Super admin: all tenants. Agency admin: the agency plus its clients. Others: own tenant."""
    stmt = select(Tenant)
    if auth.role is Role.SUPER_ADMIN:
        pass
    else:
        home_id = auth.user.tenant_id
        home = await db.get(Tenant, home_id)
        if auth.role is Role.TENANT_ADMIN and home is not None and home.kind is TenantKind.AGENCY:
            stmt = stmt.where(or_(Tenant.id == home_id, Tenant.parent_id == home_id))
        else:
            stmt = stmt.where(Tenant.id == home_id)
    if q:
        stmt = stmt.where(Tenant.name.ilike(f"%{q}%"))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Tenant.created_at).limit(limit).offset(offset))).scalars().all()
    return list(rows), int(total)


async def get_accessible_tenant(db: AsyncSession, auth: AuthContext, tenant_id: uuid.UUID) -> Tenant:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None or not await can_access_tenant(db, auth.user, tenant):
        raise NotFoundError("Tenant not found")  # don't reveal existence
    return tenant


async def update_tenant(
    db: AsyncSession, auth: AuthContext, tenant: Tenant, data: TenantUpdate, request: Request | None
) -> Tenant:
    changes = data.model_dump(exclude_unset=True)
    if "status" in changes or "plan" in changes:
        # Suspending / re-planning is a platform or agency (parent) decision.
        is_parent_agency = tenant.parent_id is not None and tenant.parent_id == auth.user.tenant_id
        if auth.role is not Role.SUPER_ADMIN and not is_parent_agency:
            raise ForbiddenError("Only a super admin or the owning agency can change status or plan")
    if tenant.kind is TenantKind.PLATFORM and changes.get("status") == TenantStatus.SUSPENDED:
        raise ForbiddenError("The platform tenant cannot be suspended")

    for field in ("name", "industry", "plan", "status"):
        if field in changes and changes[field] is not None:
            setattr(tenant, field, changes[field])
    if data.settings is not None:
        tenant.settings = data.settings.model_dump(mode="json")

    async with unscoped(db):
        audit_service.record(
            db,
            tenant_id=tenant.id,
            action="tenant.update",
            resource_type="tenant",
            resource_id=tenant.id,
            actor=auth.user,
            request=request,
            details={"fields": sorted(changes)},
        )
        await db.commit()
        await db.refresh(tenant)
    return tenant


async def update_branding(
    db: AsyncSession, auth: AuthContext, branding: Branding, request: Request | None
) -> Branding:
    tenant = auth.tenant
    tenant.branding = branding.model_dump(mode="json")
    audit_service.record(
        db,
        tenant_id=tenant.id,
        action="tenant.branding.update",
        resource_type="tenant",
        resource_id=tenant.id,
        actor=auth.user,
        request=request,
    )
    await db.commit()
    return branding


async def client_usage(db: AsyncSession, agency_id: uuid.UUID) -> list[TenantUsage]:
    """Per-client summary for the reseller dashboard (extended with token usage in phase 6)."""
    async with unscoped(db):
        clients = (
            (await db.execute(select(Tenant).where(Tenant.parent_id == agency_id).order_by(Tenant.name)))
            .scalars()
            .all()
        )
        result: list[TenantUsage] = []
        for client in clients:
            users = (
                await db.execute(select(func.count(User.id)).where(User.tenant_id == client.id))
            ).scalar_one()
            agents = (
                await db.execute(select(func.count(Agent.id)).where(Agent.tenant_id == client.id))
            ).scalar_one()
            result.append(TenantUsage(tenant_id=client.id, name=client.name, users=users, agents=agents))
    return result
