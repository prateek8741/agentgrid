from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import DB, AdminAuth
from app.schemas.audit import AuditLogOut
from app.schemas.common import Page
from app.services import audit_service

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut])
async def list_audit_logs(
    auth: AdminAuth,
    db: DB,
    action: str | None = Query(
        default=None, max_length=80, description="Filter by action prefix, e.g. 'user.'"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> Page[AuditLogOut]:
    rows, total = await audit_service.list_logs(db, auth.tenant_id, action=action, limit=limit, offset=offset)
    return Page(items=[AuditLogOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset)
