"""Lightweight workflow engine: durable actions with retries, backoff and timers.

* `enqueue()` stages an `ActionLog` row in the caller's transaction, so the
  action exists if and only if the business change committed (outbox pattern).
* The engine claims due rows with a conditional UPDATE (safe with several
  workers/replicas), runs the handler, and on failure reschedules with
  exponential backoff until `max_attempts`, then marks the action failed.
* Timers (e.g. appointment reminders) are just actions with a future `run_at`.
* No busy polling: the loop sleeps until the next due action (max 60s) and is
  woken immediately when new work is enqueued in-process.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger
from app.core.tenancy import set_session_tenant
from app.models import ActionLog, ActionStatus, Tenant

log = get_logger(__name__)

STUCK_AFTER = timedelta(minutes=10)
HANDLER_TIMEOUT_SECONDS = 60
MAX_IDLE_SLEEP = 60.0


class PermanentActionError(Exception):
    """Raise from a handler when retrying cannot help (bad input, missing record)."""


@dataclass
class ActionContext:
    action: ActionLog
    db: AsyncSession  # bound to the action's tenant
    tenant: Tenant

    @property
    def payload(self) -> dict[str, Any]:
        return self.action.payload


ActionHandler = Callable[[ActionContext], Awaitable[dict[str, Any] | None]]
_HANDLERS: dict[str, ActionHandler] = {}


def action_handler(action_type: str) -> Callable[[ActionHandler], ActionHandler]:
    def register(fn: ActionHandler) -> ActionHandler:
        _HANDLERS[action_type] = fn
        return fn

    return register


def _load_builtin_handlers() -> None:
    """Register the built-in handlers regardless of what the entrypoint imported (API, CLI, worker)."""
    from app.workers import actions, maintenance  # noqa: F401


def registered_actions() -> list[str]:
    return sorted(_HANDLERS)


def backoff_seconds(attempts: int) -> float:
    """2s, 4s, 8s ... capped at 10 minutes, plus jitter."""
    return min(2.0**attempts, 600.0) + random.uniform(0, 1)  # noqa: S311 - jitter, not crypto


def enqueue(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    action_type: str,
    payload: dict[str, Any],
    run_at: datetime | None = None,
    max_attempts: int = 5,
    correlation_id: str | None = None,
) -> ActionLog:
    """Stage an action in the caller's transaction. Call `engine.wake()` after commit."""
    if action_type not in _HANDLERS:
        _load_builtin_handlers()
    if action_type not in _HANDLERS:
        raise ValueError(f"Unknown action type: {action_type}")
    action = ActionLog(
        tenant_id=tenant_id,
        action_type=action_type,
        payload=payload,
        run_at=run_at or datetime.now(UTC),
        max_attempts=max_attempts,
        correlation_id=correlation_id,
    )
    db.add(action)
    return action


class WorkflowEngine:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None = None) -> None:
        if session_factory is None:
            from app.core.database import SessionLocal

            session_factory = SessionLocal
        self._sessions = session_factory
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._stopping = False

    # ---------------------------------------------------------------- execution
    async def execute(self, action_id: uuid.UUID) -> ActionStatus | None:
        """Claim and run one action. Returns the new status, or None if another worker owns it."""
        now = datetime.now(UTC)
        async with self._sessions() as db:
            claimed = await db.execute(
                update(ActionLog)
                .where(
                    ActionLog.id == action_id,
                    ActionLog.status == ActionStatus.PENDING,
                    ActionLog.run_at <= now,
                )
                .values(status=ActionStatus.RUNNING, started_at=now, attempts=ActionLog.attempts + 1)
                .execution_options(synchronize_session=False)
            )
            if claimed.rowcount != 1:
                return None
            await db.commit()

            action = await db.get(ActionLog, action_id)
            tenant = await db.get(Tenant, action.tenant_id) if action else None
            if action is None or tenant is None:
                return None
            set_session_tenant(db, tenant.id)
            handler = _HANDLERS.get(action.action_type)
            try:
                if handler is None:
                    raise PermanentActionError(f"No handler registered for {action.action_type}")
                result = await asyncio.wait_for(
                    handler(ActionContext(action, db, tenant)), HANDLER_TIMEOUT_SECONDS
                )
                action.status = ActionStatus.SUCCEEDED
                action.result = result or {}
                action.last_error = None
                action.finished_at = datetime.now(UTC)
            except Exception as exc:
                await db.rollback()  # discard partial side effects of the failed handler
                action = await db.get(ActionLog, action_id, populate_existing=True)
                if action is None:
                    return None
                error = f"{type(exc).__name__}: {exc}"[:2000]
                action.last_error = error
                if isinstance(exc, PermanentActionError) or action.attempts >= action.max_attempts:
                    action.status = ActionStatus.FAILED
                    action.finished_at = datetime.now(UTC)
                    log.warning(
                        "action failed permanently", extra={"action": action.action_type, "error": error}
                    )
                else:
                    action.status = ActionStatus.PENDING
                    action.run_at = datetime.now(UTC) + timedelta(seconds=backoff_seconds(action.attempts))
                    log.info(
                        "action will retry", extra={"action": action.action_type, "attempt": action.attempts}
                    )
            await db.commit()
            return action.status

    async def run_due(self, limit: int = 20) -> int:
        """Execute every due action (up to `limit`). Returns how many were attempted."""
        now = datetime.now(UTC)
        async with self._sessions() as db:
            # Recover actions orphaned by a crashed worker.
            await db.execute(
                update(ActionLog)
                .where(ActionLog.status == ActionStatus.RUNNING, ActionLog.started_at < now - STUCK_AFTER)
                .values(status=ActionStatus.PENDING)
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            ids = (
                (
                    await db.execute(
                        select(ActionLog.id)
                        .where(ActionLog.status == ActionStatus.PENDING, ActionLog.run_at <= now)
                        .order_by(ActionLog.run_at)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
        for action_id in ids:
            try:
                await self.execute(action_id)
            except Exception:
                log.exception("workflow engine error executing action %s", action_id)
        return len(ids)

    async def _seconds_until_next(self) -> float:
        async with self._sessions() as db:
            next_run = (
                await db.execute(
                    select(func.min(ActionLog.run_at)).where(ActionLog.status == ActionStatus.PENDING)
                )
            ).scalar_one_or_none()
        if next_run is None:
            return MAX_IDLE_SLEEP
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=UTC)
        return max(0.2, min(MAX_IDLE_SLEEP, (next_run - datetime.now(UTC)).total_seconds()))

    # ---------------------------------------------------------------- lifecycle
    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        log.info("workflow engine started")
        while not self._stopping:
            try:
                attempted = await self.run_due()
                sleep_for = 0.0 if attempted >= 20 else await self._seconds_until_next()
            except Exception:
                log.exception("workflow loop error")
                sleep_for = 5.0
            if sleep_for > 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=sleep_for)
            self._wake.clear()

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.create_task(self._loop(), name="workflow-engine")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None


_engine: WorkflowEngine | None = None


def get_workflow_engine() -> WorkflowEngine:
    global _engine
    if _engine is None:
        _engine = WorkflowEngine()
    return _engine
