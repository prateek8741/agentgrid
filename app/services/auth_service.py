"""Login, refresh-token rotation (with reuse detection), logout and tenant switching."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, can_access_tenant, client_ip
from app.core.config import settings
from app.core.errors import AuthError, ForbiddenError, NotFoundError
from app.core.logging import get_logger
from app.core.rbac import Role
from app.core.security import (
    TokenError,
    create_token,
    decode_token,
    hash_password,
    verify_password,
    verify_password_dummy,
)
from app.core.tenancy import unscoped
from app.models import RefreshToken, Tenant, User
from app.schemas.auth import TenantBrief, TokenResponse
from app.schemas.user import UserOut
from app.services import audit_service

log = get_logger(__name__)


async def _issue_tokens(
    db: AsyncSession,
    user: User,
    tenant: Tenant,
    request: Request | None,
    family_id: uuid.UUID | None = None,
) -> TokenResponse:
    family_id = family_id or uuid.uuid4()
    access, _ = create_token(
        user_id=user.id,
        tenant_id=tenant.id,
        home_tenant_id=user.tenant_id,
        role=user.role.value,
        token_type="access",
    )
    refresh, refresh_payload = create_token(
        user_id=user.id,
        tenant_id=tenant.id,
        home_tenant_id=user.tenant_id,
        role=user.role.value,
        token_type="refresh",
        family_id=family_id,
    )
    db.add(
        RefreshToken(
            user_id=user.id,
            jti=refresh_payload.jti,
            family_id=family_id,
            expires_at=refresh_payload.exp,
            ip=client_ip(request) if request else None,
            user_agent=(request.headers.get("user-agent") or "")[:300] if request else None,
        )
    )
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.jwt_access_ttl_minutes * 60,
        user=UserOut.model_validate(user),
        tenant=TenantBrief(id=tenant.id, name=tenant.name, slug=tenant.slug, kind=tenant.kind.value),
    )


async def login(db: AsyncSession, email: str, password: str, request: Request | None) -> TokenResponse:
    user = (await db.execute(select(User).where(User.email == email.lower()))).scalar_one_or_none()
    if user is None:
        await verify_password_dummy()  # equalise timing to avoid user enumeration
        raise AuthError("Invalid email or password")
    if not await verify_password(password, user.password_hash):
        raise AuthError("Invalid email or password")
    if not user.is_active:
        raise AuthError("Account is disabled")
    tenant = await db.get(Tenant, user.tenant_id)
    if tenant is None or (not tenant.is_active and user.role is not Role.SUPER_ADMIN):
        raise ForbiddenError("Tenant is suspended")

    user.last_login_at = datetime.now(UTC)
    response = await _issue_tokens(db, user, tenant, request)
    audit_service.record(
        db,
        tenant_id=tenant.id,
        action="auth.login",
        resource_type="user",
        resource_id=user.id,
        actor=user,
        request=request,
    )
    await db.commit()
    return response


async def _revoke_family(db: AsyncSession, family_id: uuid.UUID) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )


async def refresh(db: AsyncSession, raw_token: str, request: Request | None) -> TokenResponse:
    try:
        payload = decode_token(raw_token, "refresh")
    except TokenError as exc:
        raise AuthError(str(exc)) from exc

    row = (await db.execute(select(RefreshToken).where(RefreshToken.jti == payload.jti))).scalar_one_or_none()
    if row is None:
        raise AuthError("Unknown refresh token")
    if row.revoked_at is not None:
        # A rotated token was replayed: assume theft and kill the whole session family.
        await _revoke_family(db, row.family_id)
        await db.commit()
        log.warning("refresh token reuse detected; family revoked", extra={"user_id": str(row.user_id)})
        raise AuthError("Refresh token has been revoked")

    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AuthError("User is inactive")
    tenant = await db.get(Tenant, payload.tid)
    if tenant is None or not await can_access_tenant(db, user, tenant):
        raise AuthError("Tenant access revoked")
    if not tenant.is_active and user.role is not Role.SUPER_ADMIN:
        raise ForbiddenError("Tenant is suspended")

    row.revoked_at = datetime.now(UTC)
    response = await _issue_tokens(db, user, tenant, request, family_id=row.family_id)
    await db.commit()
    return response


async def logout(db: AsyncSession, raw_token: str) -> None:
    try:
        payload = decode_token(raw_token, "refresh")
    except TokenError:
        return  # logging out with a bad token is a no-op
    row = (await db.execute(select(RefreshToken).where(RefreshToken.jti == payload.jti))).scalar_one_or_none()
    if row is not None:
        await _revoke_family(db, row.family_id)
        await db.commit()


async def switch_tenant(
    db: AsyncSession, auth: AuthContext, target_id: uuid.UUID, request: Request | None
) -> TokenResponse:
    async with unscoped(db):
        target = await db.get(Tenant, target_id)
        if target is None:
            raise NotFoundError("Tenant not found")
        if not await can_access_tenant(db, auth.user, target):
            raise ForbiddenError("No access to this tenant")
        response = await _issue_tokens(db, auth.user, target, request)
        audit_service.record(
            db,
            tenant_id=target.id,
            action="auth.switch_tenant",
            resource_type="tenant",
            resource_id=target.id,
            actor=auth.user,
            request=request,
            details={"from_tenant_id": str(auth.tenant_id)},
        )
        await db.commit()
    return response


async def change_password(
    db: AsyncSession, auth: AuthContext, current: str, new: str, request: Request | None
) -> None:
    if not await verify_password(current, auth.user.password_hash):
        raise AuthError("Current password is incorrect")
    async with unscoped(db):  # the user row lives in the home tenant
        auth.user.password_hash = await hash_password(new)
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == auth.user.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        audit_service.record(
            db,
            tenant_id=auth.user.tenant_id,
            action="auth.change_password",
            resource_type="user",
            resource_id=auth.user.id,
            actor=auth.user,
            request=request,
        )
        await db.commit()
