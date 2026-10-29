"""Fixed-window rate limiting backed by Redis (INCR + EXPIRE, 1 round trip)."""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.core.errors import RateLimitedError
from app.core.logging import get_logger
from app.core.redis import get_redis

log = get_logger(__name__)


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


async def hit(scope: str, identity: str, limit: int, window_seconds: int = 60) -> RateLimitResult:
    """Count one hit for `identity` in `scope`. Fails open if Redis is unavailable."""
    now = int(time.time())
    window = now // window_seconds
    key = f"rl:{scope}:{identity}:{window}"
    try:
        pipe = get_redis().pipeline(transaction=True)
        pipe.incr(key)
        pipe.expire(key, window_seconds + 1)
        count, _ = await pipe.execute()
    except Exception as exc:  # availability over strictness for a rate limiter
        log.warning("rate limiter unavailable, failing open: %s", type(exc).__name__)
        return RateLimitResult(True, limit, limit, 0)
    remaining = max(0, limit - int(count))
    retry_after = window_seconds - (now % window_seconds)
    return RateLimitResult(int(count) <= limit, limit, remaining, retry_after)


async def enforce(scope: str, identity: str, limit: int, window_seconds: int = 60) -> RateLimitResult:
    result = await hit(scope, identity, limit, window_seconds)
    if not result.allowed:
        raise RateLimitedError(
            f"Too many requests. Try again in {result.retry_after}s.", retry_after=result.retry_after
        )
    return result
