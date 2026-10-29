"""Audit trail for administrative actions."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, request_id_var
from app.models import AuditLog, User

log = get_logger(__name__)


def record(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: object | None = None,
    actor: User | None = None,
    details: dict[str, Any] | None = None,
    request: Request | None = None,
) -> AuditLog:
    """Stage an audit entry in the caller's transaction (committed with the change itself)."""
    ip = ua = None
    if request is not None:
        forwarded = request.headers.get("x-forwarded-for")
        ip = (
            forwarded.split(",")[0].strip()
            if forwarded
            else (request.client.host if request.client else None)
        )
        ua = (request.headers.get("user-agent") or "")[:300] or None
    entry = AuditLog(
        tenant_id=tenant_id,
        actor_user_id=actor.id if actor else None,
        actor_email=actor.email if actor else None,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        details=details or {},
        ip=ip,
        user_agent=ua,
        request_id=request_id_var.get(),
    )
    db.add(entry)
    log.info("audit %s %s %s", action, resource_type, resource_id or "", extra={"audit_action": action})
    return entry


async def list_logs(
    db: AsyncSession, tenant_id: uuid.UUID, *, action: str | None, limit: int, offset: int
) -> tuple[list[AuditLog], int]:
    base = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
    if action:
        base = base.where(AuditLog.action.startswith(action))
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (await db.execute(base.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return list(rows), int(total)
