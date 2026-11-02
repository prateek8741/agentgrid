"""Scheduled maintenance: per-tenant data retention (auto-delete old transcripts).

Runs as a self-rescheduling workflow action (daily at ~03:00 UTC), so it is
durable, visible in the action log and safe with several workers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select

from app.core.config import settings
from app.core.logging import get_logger
from app.core.tenancy import unscoped
from app.models import ActionLog, ActionStatus, Conversation, Tenant, TenantKind
from app.workers.workflow import ActionContext, action_handler, enqueue

log = get_logger(__name__)
RETENTION_ACTION = "maintenance.retention"


def _next_run(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    run = now.replace(hour=3, minute=0, second=0, microsecond=0)
    return run if run > now else run + timedelta(days=1)


async def apply_retention(db: Any, now: datetime | None = None) -> dict[str, int]:
    """Delete conversations (and, by cascade, their messages) older than each tenant's retention."""
    now = now or datetime.now(UTC)
    deleted: dict[str, int] = {}
    async with unscoped(db):
        tenants = (await db.execute(select(Tenant).where(Tenant.kind != TenantKind.PLATFORM))).scalars().all()
        for tenant in tenants:
            days = int((tenant.settings or {}).get("retention_days", settings.default_retention_days))
            cutoff = now - timedelta(days=days)
            result = await db.execute(
                delete(Conversation)
                .where(Conversation.tenant_id == tenant.id, Conversation.last_message_at < cutoff)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount:
                deleted[tenant.slug] = int(result.rowcount)
    return deleted


@action_handler(RETENTION_ACTION)
async def _retention(ctx: ActionContext) -> dict[str, Any]:
    deleted = await apply_retention(ctx.db)
    enqueue(
        ctx.db,
        tenant_id=ctx.tenant.id,
        action_type=RETENTION_ACTION,
        payload={},
        run_at=_next_run(),
        max_attempts=3,
        correlation_id="retention",
    )
    log.info("retention run complete", extra={"deleted": deleted})
    return {"deleted_conversations": deleted}


async def ensure_scheduled() -> None:
    """Make sure exactly one pending retention job exists (owned by the platform tenant)."""
    from app.core.database import SessionLocal

    async with SessionLocal() as db:
        platform = (
            await db.execute(select(Tenant).where(Tenant.kind == TenantKind.PLATFORM))
        ).scalar_one_or_none()
        if platform is None:
            return
        pending = (
            await db.execute(
                select(ActionLog.id).where(
                    ActionLog.action_type == RETENTION_ACTION, ActionLog.status == ActionStatus.PENDING
                )
            )
        ).first()
        if pending is None:
            enqueue(
                db,
                tenant_id=platform.id,
                action_type=RETENTION_ACTION,
                payload={},
                run_at=_next_run(),
                max_attempts=3,
                correlation_id="retention",
            )
            await db.commit()
