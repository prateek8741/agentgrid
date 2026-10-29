"""Async SQLAlchemy engine and session factory.

The pool is deliberately small and bounded (DB_POOL_SIZE + DB_MAX_OVERFLOW) so
the app stays within Neon / Supabase free-tier connection limits and the
`max_connections=50` used for Postgres in docker-compose.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core import tenancy  # noqa: F401 - registers tenant-isolation session events
from app.core.config import settings


def build_engine(url: str | None = None, **overrides: Any) -> AsyncEngine:
    if url is None:
        url, connect_args = settings.async_database_url()
    else:
        connect_args = {}

    if url.startswith("sqlite"):
        kwargs: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url:
            kwargs["poolclass"] = StaticPool
        engine = create_async_engine(url, echo=settings.db_echo, **kwargs, **overrides)

        @event.listens_for(engine.sync_engine, "connect")
        def _sqlite_pragmas(dbapi_conn: Any, _record: Any) -> None:
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine

    return create_async_engine(
        url,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
        connect_args={**connect_args, "server_settings": {"application_name": "agentgrid"}},
        **overrides,
    )


engine: AsyncEngine = build_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


def configure_engine(new_engine: AsyncEngine) -> None:
    """Swap the global engine (used by tests and CLI tools)."""
    global engine
    engine = new_engine
    SessionLocal.configure(bind=new_engine)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, rolled back on error."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def ping_database() -> bool:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True
