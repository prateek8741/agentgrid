from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, Request, Response, status

from app.api.deps import DB, AdminAuth, AgentAuth
from app.schemas.common import Page
from app.schemas.user import UserCreate, UserOut, UserUpdate
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=Page[UserOut])
async def list_users(
    auth: AgentAuth,
    db: DB,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[UserOut]:
    rows, total = await user_service.list_users(db, auth, limit=limit, offset=offset)
    return Page(items=[UserOut.model_validate(u) for u in rows], total=total, limit=limit, offset=offset)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, request: Request, auth: AdminAuth, db: DB) -> UserOut:
    return UserOut.model_validate(await user_service.create_user(db, auth, body, request))


@router.get("/{user_id}", response_model=UserOut)
async def get_user(user_id: uuid.UUID, auth: AgentAuth, db: DB) -> UserOut:
    return UserOut.model_validate(await user_service.get_user(db, auth, user_id))


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, request: Request, auth: AdminAuth, db: DB
) -> UserOut:
    return UserOut.model_validate(await user_service.update_user(db, auth, user_id, body, request))


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(user_id: uuid.UUID, request: Request, auth: AdminAuth, db: DB) -> Response:
    await user_service.delete_user(db, auth, user_id, request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
