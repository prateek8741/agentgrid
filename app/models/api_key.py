from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TenantScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin


class ApiKeyKind(StrEnum):
    PUBLIC = "public"  # embedded in the web widget; can only start chats
    SECRET = "secret"  # server-to-server REST channel; never exposed to browsers


class ApiKey(UUIDPrimaryKeyMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "api_keys"

    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[ApiKeyKind] = mapped_column(
        Enum(
            ApiKeyKind,
            name="api_key_kind",
            native_enum=False,
            create_constraint=True,
            length=10,
            values_callable=lambda e: [m.value for m in e],
        )
    )
    prefix: Mapped[str] = mapped_column(String(16), index=True)
    # SHA-256 of the full key. Secret keys are only ever shown once at creation.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Public keys are public by design (they live in the widget snippet), so the
    # plaintext is kept to let admins copy the embed code at any time.
    public_key: Mapped[str | None] = mapped_column(String(80), unique=True, default=None)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
    last_used_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None
