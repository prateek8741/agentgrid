from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.rbac import Role
from app.schemas.common import ORMModel


class UserOut(ORMModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str
    role: Role
    is_active: bool
    last_login_at: datetime | None
    created_at: datetime


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(default="", max_length=200)
    role: Role = Role.AGENT
    password: str

    @field_validator("password")
    @classmethod
    def _strong(cls, v: str) -> str:
        from app.schemas.auth import validate_password_strength

        return validate_password_strength(v)

    @field_validator("email")
    @classmethod
    def _lower(cls, v: str) -> str:
        return v.lower()


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, max_length=200)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = None

    @field_validator("password")
    @classmethod
    def _strong(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from app.schemas.auth import validate_password_strength

        return validate_password_strength(v)
