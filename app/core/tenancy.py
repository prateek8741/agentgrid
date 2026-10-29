"""Tenant isolation enforced at the ORM layer (defence in depth).

Services always filter by tenant explicitly, but as a safety net every session
that carries `session.info["tenant_id"]` gets:

* an automatic `tenant_id = :tid` criterion on every SELECT / UPDATE / DELETE
  touching a `TenantScopedMixin` model (including joins and relationship loads);
* a flush-time check that new or modified tenant-scoped rows belong to that
  tenant - writing into another tenant raises `TenantIsolationError`.

Platform-level code (login, super-admin tenant management, background jobs)
uses sessions without a tenant id, or opts out per statement with
`execution_options(skip_tenant_filter=True)`.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.models.base import TenantScopedMixin

TENANT_KEY = "tenant_id"


class TenantIsolationError(PermissionError):
    pass


def set_session_tenant(session: Any, tenant_id: uuid.UUID | None) -> None:
    """Bind a (sync or async) session to a tenant."""
    session.info[TENANT_KEY] = tenant_id


def get_session_tenant(session: Any) -> uuid.UUID | None:
    return session.info.get(TENANT_KEY)


@event.listens_for(Session, "do_orm_execute")
def _apply_tenant_criteria(state: ORMExecuteState) -> None:
    tenant_id = state.session.info.get(TENANT_KEY)
    if tenant_id is None or state.execution_options.get("skip_tenant_filter"):
        return
    if state.is_column_load or state.is_relationship_load:
        return  # criteria propagate from the parent statement
    if state.is_select or state.is_update or state.is_delete:
        state.statement = state.statement.options(
            with_loader_criteria(
                TenantScopedMixin,
                lambda cls: cls.tenant_id == tenant_id,
                include_aliases=True,
            )
        )


@event.listens_for(Session, "before_flush")
def _stamp_and_verify_tenant(session: Session, _ctx: Any, _instances: Any) -> None:
    tenant_id = session.info.get(TENANT_KEY)
    if tenant_id is None:
        return
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, TenantScopedMixin):
            continue
        if obj.tenant_id is None:
            obj.tenant_id = tenant_id
        elif obj.tenant_id != tenant_id:
            raise TenantIsolationError(
                f"Refusing to write {type(obj).__name__} for another tenant in a tenant-bound session"
            )


class unscoped:
    """Temporarily lift the tenant binding of a session for platform operations
    (e.g. a super admin or agency admin provisioning another tenant)."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self._previous: uuid.UUID | None = None

    async def __aenter__(self) -> Any:
        self._previous = get_session_tenant(self.session)
        set_session_tenant(self.session, None)
        return self.session

    async def __aexit__(self, *_exc: object) -> None:
        set_session_tenant(self.session, self._previous)
