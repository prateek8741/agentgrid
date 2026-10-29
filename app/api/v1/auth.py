from __future__ import annotations

from fastapi import APIRouter, Request, status

from app.api.deps import DB, CurrentAuth, client_ip
from app.core.config import settings
from app.core.rate_limit import enforce
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    SwitchTenantRequest,
    TenantBrief,
    TokenResponse,
)
from app.schemas.common import Message
from app.schemas.tenant import Branding, TenantSettings
from app.schemas.user import UserOut
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse, summary="Log in with email + password")
async def login(body: LoginRequest, request: Request, db: DB) -> TokenResponse:
    limit = settings.login_rate_limit_per_minute
    await enforce("login-ip", client_ip(request), limit)
    await enforce("login-email", body.email.lower(), limit)
    return await auth_service.login(db, body.email, body.password, request)


@router.post("/refresh", response_model=TokenResponse, summary="Rotate a refresh token")
async def refresh(body: RefreshRequest, request: Request, db: DB) -> TokenResponse:
    await enforce("refresh-ip", client_ip(request), settings.login_rate_limit_per_minute * 3)
    return await auth_service.refresh(db, body.refresh_token, request)


@router.post("/logout", response_model=Message, summary="Revoke a refresh token (and its session family)")
async def logout(body: RefreshRequest, db: DB) -> Message:
    await auth_service.logout(db, body.refresh_token)
    return Message(message="Logged out")


@router.get("/me", summary="Current user, active tenant and its branding")
async def me(auth: CurrentAuth) -> dict[str, object]:
    return {
        "user": UserOut.model_validate(auth.user),
        "tenant": TenantBrief(
            id=auth.tenant.id, name=auth.tenant.name, slug=auth.tenant.slug, kind=auth.tenant.kind.value
        ),
        "home_tenant_id": auth.user.tenant_id,
        "role": auth.role.value,
        "branding": Branding.model_validate(auth.tenant.branding or {}),
        "settings": TenantSettings.model_validate(auth.tenant.settings or {}),
    }


@router.post(
    "/switch-tenant",
    response_model=TokenResponse,
    summary="Act inside a client tenant (agency / super admin)",
)
async def switch_tenant(
    body: SwitchTenantRequest, request: Request, auth: CurrentAuth, db: DB
) -> TokenResponse:
    return await auth_service.switch_tenant(db, auth, body.tenant_id, request)


@router.post("/change-password", response_model=Message, status_code=status.HTTP_200_OK)
async def change_password(
    body: ChangePasswordRequest, request: Request, auth: CurrentAuth, db: DB
) -> Message:
    await enforce("change-password", str(auth.user.id), 5)
    await auth_service.change_password(db, auth, body.current_password, body.new_password, request)
    return Message(message="Password changed; other sessions were signed out")
