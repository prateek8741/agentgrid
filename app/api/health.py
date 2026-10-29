"""Liveness / readiness probes (unauthenticated, used by Docker, Render, k8s)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import ping_database
from app.core.logging import get_logger
from app.core.redis import ping_redis, redis_mode

router = APIRouter(tags=["health"])
log = get_logger(__name__)


@router.get("/health", summary="Liveness probe")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name}


@router.get("/ready", summary="Readiness probe (database + redis)")
async def ready() -> JSONResponse:
    checks: dict[str, str] = {}

    async def _check(name: str, coro_factory: object) -> None:
        try:
            await asyncio.wait_for(coro_factory(), timeout=5)  # type: ignore[operator]
            checks[name] = "ok"
        except Exception as exc:
            log.warning("readiness check %s failed: %s", name, type(exc).__name__)
            checks[name] = "error"

    await asyncio.gather(_check("database", ping_database), _check("redis", ping_redis))
    ok = all(v == "ok" for v in checks.values())
    body = {
        "status": "ready" if ok else "degraded",
        "checks": checks,
        "redis_mode": redis_mode(),
        "event_bus": "kafka" if settings.kafka_bootstrap_servers else "in-process",
    }
    return JSONResponse(status_code=200 if ok else 503, content=body)
