"""Redis client with an in-process fallback.

With REDIS_URL set (Upstash in lite mode, the `redis` container in full mode)
we use a real Redis. Without it we fall back to `fakeredis`, which implements
the same async API in memory - fine for a single local process and for tests,
but state is not shared between workers (documented in the README).
"""

from __future__ import annotations

from typing import Any

from redis.asyncio import Redis

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

_client: Redis | None = None


def _create_client() -> Redis:
    if settings.redis_url:
        return Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_timeout=5,
            socket_connect_timeout=5,
            health_check_interval=30,
            max_connections=20,
        )
    import fakeredis

    log.warning("REDIS_URL not set - using in-process fakeredis fallback (single process only)")
    return fakeredis.FakeAsyncRedis(decode_responses=True)


def get_redis() -> Redis:
    global _client
    if _client is None:
        _client = _create_client()
    return _client


def set_redis(client: Redis | None) -> None:
    """Override the client (tests)."""
    global _client
    _client = client


async def close_redis() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def ping_redis() -> bool:
    result: Any = await get_redis().ping()
    return bool(result)


def redis_mode() -> str:
    return "redis" if settings.redis_url else "in-memory"
