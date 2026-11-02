"""Analytics consumer, dashboard KPIs, reseller/platform views, metrics, retention, Kafka fallback."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.rbac import Role
from app.models import AnalyticsDaily, Conversation, Lead, LeadStatus, Message, Tenant, TenantKind
from app.workers.maintenance import apply_retention
from tests.conftest import auth_headers, chat, make_agent, make_slots, make_tenant, make_user


async def _metric(tenant_id, metric: str) -> float:  # type: ignore[no-untyped-def]
    async with SessionLocal() as s:
        return float(
            (
                await s.execute(
                    select(func.coalesce(func.sum(AnalyticsDaily.value), 0)).where(
                        AnalyticsDaily.tenant_id == tenant_id, AnalyticsDaily.metric == metric
                    )
                )
            ).scalar_one()
        )


async def _busy_tenant() -> Tenant:
    tenant = await make_tenant()
    await make_agent(tenant, "clinic_appointment_booker")
    await make_slots(tenant)
    for text in ("book an appointment tomorrow", "Cleaning", "1", "Meena Iyer", "9876512222", "yes"):
        await chat(tenant, text, session_id="booker")
    await chat(tenant, "hello", session_id="greeter")
    await chat(tenant, "talk to a human please", session_id="escalate")
    return tenant


async def test_consumer_writes_daily_aggregates() -> None:
    tenant = await _busy_tenant()
    assert await _metric(tenant.id, "conversations") == 3
    assert await _metric(tenant.id, "messages_in") == 8
    assert await _metric(tenant.id, "appointments") == 1
    assert await _metric(tenant.id, "handovers") == 1
    async with SessionLocal() as s:
        channels = {
            c
            for (c,) in (
                await s.execute(select(AnalyticsDaily.channel).where(AnalyticsDaily.tenant_id == tenant.id))
            ).all()
        }
    assert channels == {"web"}


async def test_rebuild_matches_live_aggregates() -> None:
    tenant = await _busy_tenant()
    live = {
        m: await _metric(tenant.id, m) for m in ("conversations", "messages_in", "appointments", "handovers")
    }
    from app.workers.analytics import rebuild_daily

    async with SessionLocal() as s:
        await rebuild_daily(s, tenant.id)
        await s.commit()
    rebuilt = {m: await _metric(tenant.id, m) for m in live}
    assert rebuilt == live


async def test_overview_kpis(client: AsyncClient) -> None:
    tenant = await _busy_tenant()
    async with SessionLocal() as s:
        s.add(Lead(tenant_id=tenant.id, name="Q", status=LeadStatus.QUALIFIED, source_channel="web"))
        s.add(Lead(tenant_id=tenant.id, name="N", source_channel="web"))
        await s.commit()
    headers = await auth_headers(client, await make_user(tenant, Role.VIEWER))
    data = (await client.get("/api/v1/analytics/overview?days=7", headers=headers)).json()
    k = data["kpis"]
    assert k["conversations"] == 3
    assert k["escalation_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert k["automated_resolution_rate"] == pytest.approx(1 / 3, abs=1e-3)  # the completed booking
    assert k["appointments_booked"] == 1 and k["appointment_conversion_rate"] == 1.0
    assert k["leads"] == 2 and k["lead_qualification_rate"] == 0.5
    assert (
        k["first_response_ms"]["avg"] is not None and k["first_response_ms"]["web_within_target_rate"] == 1.0
    )
    assert len(data["daily"]) == 7 and data["daily"][-1]["conversations"] == 3
    assert data["by_channel"] == {"web": 3}
    assert any(i["intent"] == "book_appointment" for i in data["intents"])
    assert data["handover_reasons"] == {"customer_request": 1}
    assert data["quota"]["monthly_quota"] > 0


async def test_agency_and_platform_views(client: AsyncClient) -> None:
    agency = await make_tenant(kind=TenantKind.AGENCY)
    client_a = await make_tenant(parent_id=agency.id)
    await make_agent(client_a, "customer_support_faq")
    await chat(client_a, "hello", session_id="a1")
    agency_admin = await auth_headers(client, await make_user(agency, Role.TENANT_ADMIN))
    rows = (await client.get("/api/v1/analytics/agency", headers=agency_admin)).json()
    assert [r["name"] for r in rows] == [client_a.name] and rows[0]["conversations"] == 1

    biz_admin = await auth_headers(client, await make_user(client_a, Role.TENANT_ADMIN))
    assert (await client.get("/api/v1/analytics/agency", headers=biz_admin)).status_code == 403
    assert (await client.get("/api/v1/analytics/platform", headers=biz_admin)).status_code == 403
    platform = await make_tenant(kind=TenantKind.PLATFORM)
    root = await auth_headers(client, await make_user(platform, Role.SUPER_ADMIN))
    names = {r["name"] for r in (await client.get("/api/v1/analytics/platform", headers=root)).json()}
    assert {agency.name, client_a.name} <= names and platform.name not in names


async def test_metrics_endpoint(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from pydantic import SecretStr

    tenant = await make_tenant()
    await make_agent(tenant, "customer_support_faq")
    await chat(tenant, "talk to a human", session_id="m1")
    await client.get("/health")
    body = (await client.get("/metrics")).text
    assert "agentgrid_http_requests_total" in body and 'route="/health"' in body
    assert "agentgrid_handovers_total" in body and "agentgrid_agent_replies_total" in body
    monkeypatch.setattr(settings, "metrics_token", SecretStr("s3cret"))
    assert (await client.get("/metrics")).status_code == 401
    assert (await client.get("/metrics", headers={"Authorization": "Bearer s3cret"})).status_code == 200


async def test_retention_deletes_old_transcripts_per_tenant() -> None:
    short, long_ = await make_tenant(), await make_tenant()
    for tenant, days in ((short, 30), (long_, 365)):
        await make_agent(tenant, "customer_support_faq")
        await chat(tenant, "hello", session_id="old")
        async with SessionLocal() as s:
            t = await s.get(Tenant, tenant.id)
            t.settings = {**t.settings, "retention_days": days}
            conv = (
                await s.execute(select(Conversation).where(Conversation.tenant_id == tenant.id))
            ).scalar_one()
            conv.last_message_at = datetime.now(UTC) - timedelta(days=90)
            await s.commit()
    async with SessionLocal() as s:
        deleted = await apply_retention(s)
        await s.commit()
    assert deleted.get(short.slug) == 1 and long_.slug not in deleted
    async with SessionLocal() as s:
        remaining = {t for (t,) in (await s.execute(select(Conversation.tenant_id))).all()}
        orphan_msgs = (
            await s.execute(select(func.count(Message.id)).where(Message.tenant_id == short.id))
        ).scalar_one()
    assert short.id not in remaining and long_.id in remaining and orphan_msgs == 0


async def test_kafka_bus_falls_back_to_in_process_when_broker_down(monkeypatch: pytest.MonkeyPatch) -> None:
    import aiokafka

    from app.workers.events import Event
    from app.workers.kafka_bus import KafkaEventBus

    class DownProducer:
        def __init__(self, **_kw):  # type: ignore[no-untyped-def]
            pass

        async def start(self) -> None:
            raise ConnectionError("broker unreachable")

    monkeypatch.setattr(aiokafka, "AIOKafkaProducer", DownProducer)
    bus = KafkaEventBus("localhost:1", "agentgrid-test")
    received: list[str] = []

    async def handler(event: Event) -> None:
        received.append(event.type)

    bus.subscribe("lead.created", handler)
    await bus.start()
    tenant = await make_tenant()
    await bus.publish(Event(type="lead.created", tenant_id=tenant.id))
    await bus.drain()
    await bus.close()
    assert received == ["lead.created"]
