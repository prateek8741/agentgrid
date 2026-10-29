"""User management inside the active tenant."""

from __future__ import annotations

import uuid

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.rbac import Role, assignable_roles
from app.core.security import hash_password
from app.models import User
from app.schemas.user import UserCreate, UserUpdate
from app.services import audit_service


async def list_users(
    db: AsyncSession, auth: AuthContext, *, limit: int, offset: int
) -> tuple[list[User], int]:
    stmt = select(User).where(User.tenant_id == auth.tenant_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(User.created_at).limit(limit).offset(offset))).scalars().all()
    return list(rows), int(total)


async def get_user(db: AsyncSession, auth: AuthContext, user_id: uuid.UUID) -> User:
    user = (
        await db.execute(select(User).where(User.id == user_id, User.tenant_id == auth.tenant_id))
    ).scalar_one_or_none()
    if user is None:
        raise NotFoundError("User not found")
    return user


def _check_role_grant(auth: AuthContext, role: Role) -> None:
    if role not in assignable_roles(auth.role):
        raise ForbiddenError(f"You cannot assign the role {role.value}")


async def create_user(db: AsyncSession, auth: AuthContext, data: UserCreate, request: Request | None) -> User:
    _check_role_grant(auth, data.role)
    taken = (
        await db.execute(
            select(User.id).where(User.email == data.email).execution_options(skip_tenant_filter=True)
        )
    ).first()
    if taken is not None:
        raise ConflictError("A user with this email already exists")
    user = User(
        tenant_id=auth.tenant_id,
        email=data.email,
        full_name=data.full_name,
        role=data.role,
        password_hash=await hash_password(data.password),
    )
    db.add(user)
    await db.flush()
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="user.create",
        resource_type="user",
        resource_id=user.id,
        actor=auth.user,
        request=request,
        details={"email": user.email, "role": user.role.value},
    )
    await db.commit()
    await db.refresh(user)
    return user


async def update_user(
    db: AsyncSession, auth: AuthContext, user_id: uuid.UUID, data: UserUpdate, request: Request | None
) -> User:
    user = await get_user(db, auth, user_id)
    changes = data.model_dump(exclude_unset=True)
    if user.id == auth.user.id and (
        ("role" in changes and changes["role"] != user.role) or changes.get("is_active") is False
    ):
        raise ForbiddenError("You cannot change your own role or deactivate yourself")
    if user.role is Role.SUPER_ADMIN and auth.role is not Role.SUPER_ADMIN:
        raise ForbiddenError("Only super admins can modify super admins")
    if data.role is not None:
        _check_role_grant(auth, data.role)
        user.role = data.role
    if data.full_name is not None:
        user.full_name = data.full_name
    if data.is_active is not None:
        user.is_active = data.is_active
    if data.password is not None:
        user.password_hash = await hash_password(data.password)
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="user.update",
        resource_type="user",
        resource_id=user.id,
        actor=auth.user,
        request=request,
        details={
            "fields": sorted(k for k in changes if k != "password") + (["password"] if data.password else [])
        },
    )
    await db.commit()
    await db.refresh(user)
    return user


async def delete_user(
    db: AsyncSession, auth: AuthContext, user_id: uuid.UUID, request: Request | None
) -> None:
    user = await get_user(db, auth, user_id)
    if user.id == auth.user.id:
        raise ForbiddenError("You cannot delete yourself")
    if user.role is Role.SUPER_ADMIN and auth.role is not Role.SUPER_ADMIN:
        raise ForbiddenError("Only super admins can delete super admins")
    audit_service.record(
        db,
        tenant_id=auth.tenant_id,
        action="user.delete",
        resource_type="user",
        resource_id=user.id,
        actor=auth.user,
        request=request,
        details={"email": user.email},
    )
    await db.delete(user)
    await db.commit()
