from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, status

from app.api.deps import DB, AdminAuth
from app.schemas.api_key import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.services import api_key_service

router = APIRouter(prefix="/api-keys", tags=["api keys"])


@router.get("", response_model=list[ApiKeyOut])
async def list_keys(auth: AdminAuth, db: DB) -> list[ApiKeyOut]:
    return [ApiKeyOut.model_validate(k) for k in await api_key_service.list_keys(db, auth)]


@router.post(
    "",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a public (widget) or secret (REST) key",
)
async def create_key(body: ApiKeyCreate, request: Request, auth: AdminAuth, db: DB) -> ApiKeyCreated:
    return await api_key_service.create_key(db, auth, body, request)


@router.post("/{key_id}/revoke", response_model=ApiKeyOut)
async def revoke_key(key_id: uuid.UUID, request: Request, auth: AdminAuth, db: DB) -> ApiKeyOut:
    return ApiKeyOut.model_validate(await api_key_service.revoke_key(db, auth, key_id, request))
