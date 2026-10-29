from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.user import UserOut

PASSWORD_MIN = 8


def validate_password_strength(value: str) -> str:
    if len(value) < PASSWORD_MIN:
        raise ValueError(f"Password must be at least {PASSWORD_MIN} characters")
    if len(value.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes")
    if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
        raise ValueError("Password must contain letters and numbers")
    return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=2000)


class SwitchTenantRequest(BaseModel):
    tenant_id: uuid.UUID


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _strong(cls, v: str) -> str:
        return validate_password_strength(v)


class TenantBrief(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    kind: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut
    tenant: TenantBrief
