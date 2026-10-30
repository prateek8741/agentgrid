"""Test fixtures.

Tests run against SQLite by default (no services needed, works on macOS,
Windows and Linux CI). Set TEST_DATABASE_URL to a Postgres+pgvector URL to run
the same suite against Postgres (the Ubuntu CI job does this).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

# Configure the environment BEFORE the app is imported.
_TMP = Path(tempfile.mkdtemp(prefix="agentgrid-tests-"))
os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", f"sqlite+aiosqlite:///{(_TMP / 'test.db').as_posix()}"
)
os.environ["REDIS_URL"] = ""
os.environ["KAFKA_BOOTSTRAP_SERVERS"] = ""
os.environ["BCRYPT_ROUNDS"] = "4"
os.environ["LOG_JSON"] = "false"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
os.environ["GROQ_API_KEY"] = ""
os.environ["GEMINI_API_KEY"] = ""

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.core.database import SessionLocal, engine  # noqa: E402
from app.core.rbac import Role  # noqa: E402
from app.core.redis import get_redis  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Tenant, TenantKind, User  # noqa: E402
from app.services.tenant_service import provision_tenant  # noqa: E402

PASSWORD = "Passw0rd!x"


@pytest.fixture(scope="session", autouse=True)
async def _schema() -> AsyncIterator[None]:
    if engine.dialect.name == "postgresql":
        from sqlalchemy import text

        try:
            async with engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:  # pgvector-specific tests skip themselves when unavailable
            pass
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _reset_redis() -> None:
    await get_redis().flushall()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
async def db() -> AsyncIterator[object]:
    async with SessionLocal() as session:
        yield session


def unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def make_tenant(
    name: str | None = None, kind: TenantKind = TenantKind.BUSINESS, parent_id: uuid.UUID | None = None
) -> Tenant:
    async with SessionLocal() as session:
        tenant = await provision_tenant(
            session, name=name or unique("Tenant"), kind=kind, parent_id=parent_id
        )
        await session.commit()
        await session.refresh(tenant)
        return tenant


async def make_user(tenant: Tenant, role: Role, *, active: bool = True) -> User:
    async with SessionLocal() as session:
        user = User(
            tenant_id=tenant.id,
            email=f"{unique(role.value).lower()}@example.org",
            full_name=f"{role.value} user",
            role=role,
            is_active=active,
            password_hash=await hash_password(PASSWORD),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> dict[str, object]:
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()


def bearer(tokens: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def auth_headers(client: AsyncClient, user: User) -> dict[str, str]:
    return bearer(await login(client, user.email))


# ---------------------------------------------------------------- phase 2 helpers
from datetime import UTC, datetime, timedelta  # noqa: E402

from app.agents.llm.router import LLMRouter, set_llm_router  # noqa: E402
from app.agents.llm.scripted import Responder, ScriptedProvider  # noqa: E402
from app.agents.orchestrator import get_orchestrator  # noqa: E402
from app.agents.templates import get_template  # noqa: E402
from app.channels.schema import AgentReply, ContactInfo, InboundMessage  # noqa: E402
from app.models import Agent, Channel  # noqa: E402
from app.services import booking_service  # noqa: E402
from app.workers.actions import register_subscribers  # noqa: E402
from app.workers.events import get_event_bus  # noqa: E402

register_subscribers()


@pytest.fixture(autouse=True)
async def _no_llm() -> AsyncIterator[None]:
    """Never call real LLM APIs from tests; individual tests opt into a scripted model."""
    set_llm_router(LLMRouter([]))
    yield
    await get_event_bus().drain()
    set_llm_router(None)


def use_scripted_llm(responder: Responder) -> ScriptedProvider:
    provider = ScriptedProvider(responder)
    set_llm_router(LLMRouter([provider]))
    return provider


async def make_agent(tenant: Tenant, template_key: str, **overrides: object) -> Agent:
    template = get_template(template_key)
    assert template is not None
    async with SessionLocal() as session:
        await session.execute(
            Agent.__table__.update().where(Agent.tenant_id == tenant.id).values(is_default=False)
        )
        agent = Agent(
            tenant_id=tenant.id,
            name=template.name,
            is_default=True,
            **{**template.agent_fields(), **overrides},
        )
        session.add(agent)
        await session.commit()
        await session.refresh(agent)
        return agent


async def make_slots(tenant: Tenant, days: int = 7, capacity: int = 1) -> int:
    async with SessionLocal() as session:
        fresh = await session.get(Tenant, tenant.id)
        today = booking_service.local_today(fresh)
        from datetime import time

        created = await booking_service.generate_slots(
            session,
            fresh,
            start_date=today + timedelta(days=1),
            end_date=today + timedelta(days=days),
            weekdays=list(range(7)),
            day_start=time(10, 0),
            day_end=time(12, 0),
            slot_minutes=30,
            capacity=capacity,
        )
        await session.commit()
        return created


async def chat(
    tenant: Tenant, text: str, session_id: str = "s1", channel: Channel = Channel.WEB, **contact: str
) -> AgentReply:
    async with SessionLocal() as session:
        reply = await get_orchestrator().handle(
            session,
            InboundMessage(
                tenant_id=tenant.id,
                channel=channel,
                session_id=session_id,
                text=text,
                contact=ContactInfo(**contact),
            ),
        )
    await get_event_bus().drain()
    return reply


def utc_now() -> datetime:
    return datetime.now(UTC)
