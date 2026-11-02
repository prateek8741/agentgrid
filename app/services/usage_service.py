"""LLM usage metering and per-tenant monthly token quotas.

Every LLM call is recorded in `llm_usage` (source of truth for analytics and
cost). The month-to-date total is cached in Redis so the quota check on the
hot path is a single GET; on a cache miss it is recomputed from Postgres.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.llm.base import LLMResponse
from app.core.config import settings
from app.core.logging import get_logger
from app.core.metrics import LLM_LATENCY, LLM_REQUESTS, LLM_TOKENS
from app.core.redis import get_redis
from app.models import LLMUsage, Tenant

log = get_logger(__name__)
_TTL = 35 * 24 * 3600


def _month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _key(tenant_id: uuid.UUID) -> str:
    return f"usage:tokens:{tenant_id}:{datetime.now(UTC):%Y%m}"


async def _db_total(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    total = (
        await db.execute(
            select(func.coalesce(func.sum(LLMUsage.total_tokens), 0)).where(
                LLMUsage.tenant_id == tenant_id, LLMUsage.created_at >= _month_start()
            )
        )
    ).scalar_one()
    return int(total or 0)


async def tokens_this_month(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    redis = get_redis()
    try:
        cached = await redis.get(_key(tenant_id))
        if cached is not None:
            return int(cached)
    except Exception as exc:
        log.warning("usage cache unavailable: %s", type(exc).__name__)
        return await _db_total(db, tenant_id)
    total = await _db_total(db, tenant_id)
    try:
        await redis.set(_key(tenant_id), total, ex=_TTL, nx=True)
    except Exception:
        log.debug("could not prime usage cache")
    return total


@dataclass
class QuotaStatus:
    used: int
    quota: int  # 0 = unlimited

    @property
    def exceeded(self) -> bool:
        return self.quota > 0 and self.used >= self.quota

    @property
    def remaining(self) -> int | None:
        return None if self.quota == 0 else max(0, self.quota - self.used)


async def quota_status(db: AsyncSession, tenant: Tenant) -> QuotaStatus:
    quota = int((tenant.settings or {}).get("monthly_token_quota", settings.default_monthly_token_quota))
    return QuotaStatus(used=await tokens_this_month(db, tenant.id), quota=quota)


async def record(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    purpose: str,
    response: LLMResponse | None = None,
    conversation_id: uuid.UUID | None = None,
    success: bool = True,
    error_code: str | None = None,
    cached: bool = False,
    provider: str = "",
    model: str = "",
) -> LLMUsage:
    row = LLMUsage(
        tenant_id=tenant_id,
        conversation_id=conversation_id,
        purpose=purpose,
        provider=(response.provider if response else provider) or "none",
        model=(response.model if response else model) or "none",
        prompt_tokens=response.prompt_tokens if response else 0,
        completion_tokens=response.completion_tokens if response else 0,
        total_tokens=response.total_tokens if response else 0,
        latency_ms=response.latency_ms if response else 0,
        success=success,
        cached=cached,
        error_code=error_code,
    )
    db.add(row)
    if not cached:
        LLM_REQUESTS.labels(row.provider, purpose, "ok" if success else (error_code or "error")).inc()
        if response is not None:
            LLM_LATENCY.labels(row.provider, purpose).observe(response.latency_ms / 1000)
            LLM_TOKENS.labels(str(tenant_id), row.provider, "prompt").inc(row.prompt_tokens)
            LLM_TOKENS.labels(str(tenant_id), row.provider, "completion").inc(row.completion_tokens)
    if row.total_tokens:
        try:
            redis = get_redis()
            if await redis.exists(_key(tenant_id)):
                await redis.incrby(_key(tenant_id), row.total_tokens)
        except Exception:
            log.debug("usage cache increment skipped")
    return row
