"""Declarative base, portable column types and common mixins.

Types are chosen so the same models run on PostgreSQL (production, with JSONB
and pgvector) and SQLite (tests / zero-config fallback).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, MetaData, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from sqlalchemy.types import TypeDecorator

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetime that always round-trips as UTC (also on SQLite)."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        uuid.UUID: Uuid(as_uuid=True),
        datetime: UTCDateTime(),
        dict[str, Any]: JSONType,
        list[Any]: JSONType,
        list[str]: JSONType,
    }


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, nullable=False)


class TenantScopedMixin:
    """Marks a model as owned by a tenant.

    Every SELECT/UPDATE/DELETE issued through a session whose
    `session.info["tenant_id"]` is set is automatically filtered by tenant
    (see `app.core.tenancy`), and new rows are stamped / verified on flush.
    """

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:
        return mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True, nullable=False)
