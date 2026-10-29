from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext
from app.core.errors import NotFoundError
from app.core.security import generate_api_key
from app.models import ApiKey, ApiKeyKind
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.services import audit_service


async def list_keys(db: AsyncSession, auth: AuthContext) -> list[ApiKey]:
    stmt = select(ApiKey).where(ApiKey.tenant_id == auth.tenant_id).order_by(ApiKey.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


async def create_key(
    db: AsyncSession, auth: AuthContext, data: ApiKeyCreate, request: Request | None
) -> ApiKeyCreated:
    kind = ApiKeyKind(data.kind)
    raw, prefix, key_hash = generate_api_key(data.kind)
    key = ApiKey(
        tenant_id=auth.tenant_id,
        name=data.name,
        kind=kind,
        prefix=prefix,
        key_hash=key_hash,
        public_key=raw if kind is ApiKeyKind.PUBLIC else None,
        created_by_id=auth.user.id,
    )
    db.add(key)
    await db.flush()
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="api_key.create",
        resource_type="api_key",
        resource_id=key.id,
        actor=auth.user,
        request=request,
        details={"name": data.name, "kind": kind.value, "prefix": prefix},
    )
    await db.commit()
    await db.refresh(key)
    return ApiKeyCreated(**ApiKeyOut.model_validate(key).model_dump(), key=raw)


async def revoke_key(
    db: AsyncSession, auth: AuthContext, key_id: uuid.UUID, request: Request | None
) -> ApiKey:
    key = (
        await db.execute(select(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == auth.tenant_id))
    ).scalar_one_or_none()
    if key is None:
        raise NotFoundError("API key not found")
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        audit_service.record(
            db,
            tenant_id=auth.tenant_id,
            action="api_key.revoke",
            resource_type="api_key",
            resource_id=key.id,
            actor=auth.user,
            request=request,
            details={"prefix": key.prefix},
        )
        await db.commit()
        await db.refresh(key)
    return key
