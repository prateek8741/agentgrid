from __future__ import annotations

import re
import uuid
from datetime import datetime
from functools import lru_cache
from typing import Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.config import settings
from app.models.tenant import TenantKind, TenantStatus
from app.schemas.common import ORMModel

HEX_COLOR = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")


@lru_cache(maxsize=1)
def _timezones() -> frozenset[str]:
    # Needs the `tzdata` package on Windows, which has no system tz database.
    return frozenset(available_timezones())


class TenantSettings(BaseModel):
    """Per-tenant operational settings (stored as JSON on `tenants.settings`)."""

    model_config = ConfigDict(extra="ignore")

    timezone: str = "Asia/Kolkata"
    currency: str = Field(default="INR", min_length=3, max_length=3)
    retention_days: int = Field(default_factory=lambda: settings.default_retention_days, ge=1, le=3650)
    monthly_token_quota: int = Field(
        default_factory=lambda: settings.default_monthly_token_quota, ge=0, le=1_000_000_000
    )
    rate_limit_per_minute: int = Field(
        default_factory=lambda: settings.rate_limit_per_minute, ge=1, le=10_000
    )
    blocked_terms: list[str] = Field(default_factory=list, max_length=500)
    allowed_topics: list[str] = Field(default_factory=list, max_length=100)
    handover_confidence_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    notification_email: EmailStr | None = None
    outgoing_webhook_url: str | None = Field(default=None, max_length=500)
    widget_allowed_origins: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        if v not in _timezones():
            raise ValueError("Unknown timezone")
        return v

    @field_validator("outgoing_webhook_url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        if v and not v.startswith(("https://", "http://")):
            raise ValueError("Webhook URL must start with http(s)://")
        return v or None

    @field_validator("blocked_terms", "allowed_topics")
    @classmethod
    def _clean_terms(cls, v: list[str]) -> list[str]:
        return sorted({t.strip().lower()[:100] for t in v if t and t.strip()})


class Branding(BaseModel):
    """White-label branding applied to the admin console and chat widget."""

    model_config = ConfigDict(extra="ignore")

    brand_name: str | None = Field(default=None, max_length=80)
    logo_url: str | None = Field(default=None, max_length=1000)
    primary_color: str = "#4F46E5"
    accent_color: str = "#10B981"
    widget_title: str = Field(default="Chat with us", max_length=60)
    widget_subtitle: str = Field(default="We typically reply instantly", max_length=120)
    launcher_position: Literal["right", "left"] = "right"
    hide_powered_by: bool = False

    @field_validator("primary_color", "accent_color")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not HEX_COLOR.match(v):
            raise ValueError("Colour must be a hex value like #4F46E5")
        return v

    @field_validator("logo_url")
    @classmethod
    def _logo(cls, v: str | None) -> str | None:
        if v and not v.startswith(("https://", "http://", "/")):
            raise ValueError("Logo URL must be an http(s) URL or a site-relative path")
        return v or None


class TenantOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    kind: TenantKind
    status: TenantStatus
    parent_id: uuid.UUID | None
    plan: str
    industry: str | None
    settings: TenantSettings
    branding: Branding
    created_at: datetime

    @field_validator("settings", "branding", mode="before")
    @classmethod
    def _defaults(cls, v: object) -> object:
        return v or {}


class InitialAdmin(BaseModel):
    email: EmailStr
    password: str
    full_name: str = Field(default="", max_length=200)

    @field_validator("password")
    @classmethod
    def _strong(cls, v: str) -> str:
        from app.schemas.auth import validate_password_strength

        return validate_password_strength(v)


class TenantCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    slug: str | None = Field(default=None, max_length=80)
    kind: Literal["agency", "business"] = "business"
    industry: str | None = Field(default=None, max_length=80)
    plan: str = Field(default="free", max_length=40)
    parent_id: uuid.UUID | None = None
    settings: TenantSettings | None = None
    branding: Branding | None = None
    admin: InitialAdmin | None = None

    @field_validator("slug")
    @classmethod
    def _slug(cls, v: str | None) -> str | None:
        if v is not None and not SLUG.match(v):
            raise ValueError("Slug may contain lowercase letters, digits and hyphens")
        return v


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    industry: str | None = Field(default=None, max_length=80)
    plan: str | None = Field(default=None, max_length=40)
    status: TenantStatus | None = None
    settings: TenantSettings | None = None


class TenantUsage(BaseModel):
    tenant_id: uuid.UUID
    name: str
    users: int
    agents: int
