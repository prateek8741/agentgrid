from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from sqlalchemy import Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.user import User


class TenantKind(StrEnum):
    PLATFORM = "platform"  # the operator's own tenant (home of super admins)
    AGENCY = "agency"  # reseller that manages client tenants
    BUSINESS = "business"  # an end customer (direct or an agency's client)


class TenantStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


def _enum(enum_cls: type[StrEnum], name: str) -> Enum:
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=20,
        values_callable=lambda e: [m.value for m in e],
    )


class Tenant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    kind: Mapped[TenantKind] = mapped_column(_enum(TenantKind, "tenant_kind"), default=TenantKind.BUSINESS)
    status: Mapped[TenantStatus] = mapped_column(
        _enum(TenantStatus, "tenant_status"), default=TenantStatus.ACTIVE
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tenants.id", ondelete="RESTRICT"), index=True, default=None
    )
    plan: Mapped[str] = mapped_column(String(40), default="free")
    industry: Mapped[str | None] = mapped_column(String(80), default=None)
    # Validated through app.schemas.tenant.TenantSettings / Branding.
    settings: Mapped[dict[str, Any]] = mapped_column(default=dict)
    branding: Mapped[dict[str, Any]] = mapped_column(default=dict)

    parent: Mapped[Tenant | None] = relationship(remote_side="Tenant.id", lazy="raise")
    users: Mapped[list[User]] = relationship(back_populates="tenant", lazy="raise")

    @property
    def is_active(self) -> bool:
        return self.status == TenantStatus.ACTIVE
