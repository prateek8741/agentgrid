from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.rbac import Role
from app.models.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin, utcnow

if TYPE_CHECKING:
    from app.models.tenant import Tenant


class User(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[Role] = mapped_column(
        Enum(
            Role,
            name="user_role",
            native_enum=False,
            create_constraint=True,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        default=Role.VIEWER,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)

    tenant: Mapped[Tenant] = relationship(back_populates="users", lazy="raise")


class RefreshToken(UUIDPrimaryKeyMixin, Base):
    """Server-side record of issued refresh tokens (rotation + reuse detection)."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    jti: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    ip: Mapped[str | None] = mapped_column(String(64), default=None)
    user_agent: Mapped[str | None] = mapped_column(String(300), default=None)
