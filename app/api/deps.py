"""Shared FastAPI dependencies: authentication, RBAC and tenant binding.

Every protected route depends on `require_role(...)` (or `CurrentAuth`), which:
1. validates the bearer access token,
2. reloads the user and active tenant from the DB (deactivation is immediate),
3. re-checks that the user may act in the token's tenant (agency hierarchy),
4. binds the request's DB session to that tenant so all ORM queries are
   automatically tenant-filtered (see app.core.tenancy).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.errors import AuthError, ForbiddenError
from app.core.logging import tenant_id_var
from app.core.rate_limit import enforce
from app.core.rbac import Role, role_at_least
from app.core.security import (
    PUBLIC_KEY_PREFIX,
    SECRET_KEY_PREFIX,
    TokenError,
    decode_token,
    hash_api_key,
)
from app.core.tenancy import set_session_tenant
from app.models import ApiKey, ApiKeyKind, Tenant, TenantKind, User

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token from /api/v1/auth/login")

DB = Annotated[AsyncSession, Depends(get_db)]


@dataclass
class AuthContext:
    user: User
    tenant: Tenant  # the *active* tenant (may differ from user.tenant_id for agency admins)
    role: Role

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id

    @property
    def is_super_admin(self) -> bool:
        return self.role is Role.SUPER_ADMIN


def client_ip(request: Request) -> str:
    """Best-effort client IP (honours the first X-Forwarded-For hop behind a proxy)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


async def can_access_tenant(db: AsyncSession, user: User, target: Tenant) -> bool:
    """Tenant hierarchy rule: own tenant, any tenant for super admins, and
    client tenants of an agency for that agency's tenant admins."""
    if user.role is Role.SUPER_ADMIN:
        return True
    if target.id == user.tenant_id:
        return True
    if user.role is Role.TENANT_ADMIN and target.parent_id == user.tenant_id:
        home = await db.get(Tenant, user.tenant_id)
        return home is not None and home.kind is TenantKind.AGENCY
    return False


async def get_current_auth(
    request: Request,
    db: DB,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthError("Missing bearer token")
    try:
        payload = decode_token(credentials.credentials, "access")
    except TokenError as exc:
        raise AuthError(str(exc)) from exc

    user = await db.get(User, payload.sub)
    if user is None or not user.is_active:
        raise AuthError("User is inactive or no longer exists")

    tenant = await db.get(Tenant, payload.tid)
    if tenant is None:
        raise AuthError("Tenant no longer exists")
    if not tenant.is_active and user.role is not Role.SUPER_ADMIN:
        raise ForbiddenError("Tenant is suspended")
    if not await can_access_tenant(db, user, tenant):
        raise ForbiddenError("No access to this tenant")

    set_session_tenant(db, tenant.id)
    tenant_id_var.set(str(tenant.id))
    request.state.auth = AuthContext(user=user, tenant=tenant, role=Role(user.role))
    return request.state.auth


CurrentAuth = Annotated[AuthContext, Depends(get_current_auth)]


def require_role(minimum: Role) -> Callable[..., Awaitable[AuthContext]]:
    """RBAC guard: `Depends(require_role(Role.TENANT_ADMIN))`."""

    async def _guard(auth: CurrentAuth) -> AuthContext:
        if not role_at_least(auth.role, minimum):
            raise ForbiddenError(f"Requires role {minimum.value} or higher")
        return auth

    _guard.__name__ = f"require_{minimum.value}"
    _guard.__agentgrid_rbac__ = minimum.value  # type: ignore[attr-defined]
    return _guard


ViewerAuth = Annotated[AuthContext, Depends(require_role(Role.VIEWER))]
AgentAuth = Annotated[AuthContext, Depends(require_role(Role.AGENT))]
AdminAuth = Annotated[AuthContext, Depends(require_role(Role.TENANT_ADMIN))]
SuperAdminAuth = Annotated[AuthContext, Depends(require_role(Role.SUPER_ADMIN))]


# ------------------------------------------------------------------ API keys
@dataclass
class ApiKeyContext:
    key: ApiKey
    tenant: Tenant

    @property
    def tenant_id(self) -> uuid.UUID:
        return self.tenant.id


async def resolve_api_key(db: AsyncSession, raw_key: str, expected: ApiKeyKind) -> ApiKeyContext:
    prefix = PUBLIC_KEY_PREFIX if expected is ApiKeyKind.PUBLIC else SECRET_KEY_PREFIX
    if not raw_key or not raw_key.startswith(prefix) or len(raw_key) > 100:
        raise AuthError("Invalid API key")
    key = (
        await db.execute(
            select(ApiKey)
            .where(ApiKey.key_hash == hash_api_key(raw_key), ApiKey.kind == expected)
            .execution_options(skip_tenant_filter=True)
        )
    ).scalar_one_or_none()
    if key is None or not key.is_active:
        raise AuthError("Invalid or revoked API key")
    tenant = await db.get(Tenant, key.tenant_id)
    if tenant is None or not tenant.is_active:
        raise ForbiddenError("Tenant is not active")

    # Throttle last_used_at writes to once a minute per key.
    now = datetime.now(UTC)
    if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=1):
        key.last_used_at = now
        await db.commit()

    set_session_tenant(db, tenant.id)
    tenant_id_var.set(str(tenant.id))
    return ApiKeyContext(key=key, tenant=tenant)


async def get_secret_key_context(
    request: Request,
    db: DB,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> ApiKeyContext:
    """Server-to-server auth for the REST channel (secret key, per-tenant rate limit)."""
    ctx = await resolve_api_key(db, x_api_key or "", ApiKeyKind.SECRET)
    limit = int((ctx.tenant.settings or {}).get("rate_limit_per_minute", settings.rate_limit_per_minute))
    await enforce("api", str(ctx.tenant_id), limit)
    return ctx


SecretKeyAuth = Annotated[ApiKeyContext, Depends(get_secret_key_context)]
