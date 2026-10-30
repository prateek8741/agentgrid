import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select, update

from app.core.config import settings
from app.core.database import SessionLocal
from app.integrations import webhook as webhook_module
from app.integrations.http_safety import UnsafeURLError, assert_public_url
from app.models import ActionLog, ActionStatus, Appointment, Tenant
from app.workers import actions as actions_module
from app.workers.events import emit, get_event_bus
from app.workers.workflow import (
    PermanentActionError,
    WorkflowEngine,
    action_handler,
    backoff_seconds,
    enqueue,
)
from tests.conftest import chat, make_agent, make_slots, make_tenant

engine = WorkflowEngine()
_flaky_calls = {"n": 0}


@action_handler("test.flaky")
async def _flaky(ctx):  # type: ignore[no-untyped-def]
    _flaky_calls["n"] += 1
    if _flaky_calls["n"] < 3:
        raise RuntimeError("temporary outage")
    return {"ok": True}


@action_handler("test.permanent")
async def _permanent(ctx):  # type: ignore[no-untyped-def]
    raise PermanentActionError("bad input")


async def _enqueue(tenant: Tenant, action_type: str, **kwargs) -> ActionLog:  # type: ignore[no-untyped-def]
    async with SessionLocal() as s:
        action = enqueue(
            s, tenant_id=tenant.id, action_type=action_type, payload=kwargs.pop("payload", {}), **kwargs
        )
        await s.commit()
        await s.refresh(action)
        return action


async def _reload(action_id) -> ActionLog:  # type: ignore[no-untyped-def]
    async with SessionLocal() as s:
        return await s.get(ActionLog, action_id)


async def _make_due(action_id) -> None:  # type: ignore[no-untyped-def]
    async with SessionLocal() as s:
        await s.execute(update(ActionLog).where(ActionLog.id == action_id).values(run_at=datetime.now(UTC)))
        await s.commit()


def test_backoff_grows_and_is_capped() -> None:
    assert 2 <= backoff_seconds(1) < 3 and 8 <= backoff_seconds(3) < 9
    assert backoff_seconds(30) <= 601


async def test_unknown_action_type_rejected() -> None:
    tenant = await make_tenant()
    with pytest.raises(ValueError):
        await _enqueue(tenant, "does.not.exist")


async def test_retry_with_backoff_then_success() -> None:
    _flaky_calls["n"] = 0
    tenant = await make_tenant()
    action = await _enqueue(tenant, "test.flaky")
    await engine.run_due()
    first = await _reload(action.id)
    assert (
        first.status is ActionStatus.PENDING
        and first.attempts == 1
        and "temporary outage" in first.last_error
    )
    assert first.run_at > datetime.now(UTC)  # rescheduled in the future (backoff)
    assert await engine.run_due() == 0  # not due yet: nothing runs
    await _make_due(action.id)
    await engine.run_due()
    await _make_due(action.id)
    await engine.run_due()
    done = await _reload(action.id)
    assert done.status is ActionStatus.SUCCEEDED and done.attempts == 3 and done.result == {"ok": True}


async def test_permanent_error_and_max_attempts() -> None:
    tenant = await make_tenant()
    permanent = await _enqueue(tenant, "test.permanent")
    await engine.run_due()
    assert (await _reload(permanent.id)).status is ActionStatus.FAILED

    _flaky_calls["n"] = -100  # always failing for this test
    capped = await _enqueue(tenant, "test.flaky", max_attempts=2)
    await engine.run_due()
    await _make_due(capped.id)
    await engine.run_due()
    final = await _reload(capped.id)
    assert final.status is ActionStatus.FAILED and final.attempts == 2


async def test_claim_is_exclusive_and_stuck_actions_recover() -> None:
    tenant = await make_tenant()
    action = await _enqueue(tenant, "notify.team", payload={"subject": "s", "body": "b"})
    async with SessionLocal() as s:
        await s.execute(
            update(ActionLog)
            .where(ActionLog.id == action.id)
            .values(status=ActionStatus.RUNNING, started_at=datetime.now(UTC) - timedelta(hours=1))
        )
        await s.commit()
    assert await engine.execute(action.id) is None  # running elsewhere: not claimable
    await engine.run_due()  # recovers the orphan and runs it
    assert (await _reload(action.id)).status is ActionStatus.SUCCEEDED


async def test_events_fan_out_to_signed_tenant_webhook(monkeypatch: pytest.MonkeyPatch) -> None:
    tenant = await make_tenant()
    async with SessionLocal() as s:
        t = await s.get(Tenant, tenant.id)
        t.settings = {**t.settings, "outgoing_webhook_url": "https://hooks.example.org/agentgrid"}
        await s.commit()

    delivered: list[dict] = []

    async def fake_deliver(url, secret, event_type, data, event_id):  # type: ignore[no-untyped-def]
        delivered.append({"url": url, "secret": secret, "type": event_type, "data": data})
        return {"status_code": 200}

    monkeypatch.setattr(actions_module, "deliver", fake_deliver)
    await emit("lead.created", tenant.id, {"lead_id": "L1"})
    await emit("message.sent", tenant.id, {"text": "not forwarded"})
    await get_event_bus().drain()
    await engine.run_due()

    assert [d["type"] for d in delivered] == ["lead.created"]
    assert delivered[0]["secret"].startswith("whsec_")
    async with SessionLocal() as s:
        t = await s.get(Tenant, tenant.id)
        assert t.settings["outgoing_webhook_secret"] == delivered[0]["secret"]  # persisted for verification


async def test_webhook_payload_is_hmac_signed(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.content
        return httpx.Response(204)

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        webhook_module.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
    )
    await webhook_module.deliver(
        "https://hooks.example.org/x", "whsec_test", "lead.created", {"a": 1}, "evt_1"
    )
    expected = "sha256=" + hmac.new(b"whsec_test", captured["body"], hashlib.sha256).hexdigest()
    assert captured["headers"]["x-agentgrid-signature"] == expected
    assert json.loads(captured["body"]) == {"id": "evt_1", "type": "lead.created", "data": {"a": 1}}


async def test_webhook_receiver_error_raises_for_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        webhook_module.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(lambda r: httpx.Response(500)), **kw),
    )
    with pytest.raises(webhook_module.WebhookDeliveryError):
        await webhook_module.deliver("https://hooks.example.org/x", "s", "t", {}, "e")


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.org/x",
        "https://127.0.0.1/x",
        "https://169.254.169.254/latest/meta-data",
        "ftp://x.org",
    ],
)
async def test_ssrf_guard_in_production(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(UnsafeURLError):
        await assert_public_url(url)


async def test_appointment_reminder_action() -> None:
    tenant = await make_tenant()
    await make_agent(tenant, "clinic_appointment_booker")
    await make_slots(tenant)
    for text in ("book an appointment tomorrow", "Consultation", "1", "Ravi Kumar", "9876512345", "yes"):
        await chat(tenant, text, session_id="rem")
    async with SessionLocal() as s:
        reminder = (
            await s.execute(
                select(ActionLog).where(
                    ActionLog.tenant_id == tenant.id, ActionLog.action_type == "appointment.reminder"
                )
            )
        ).scalar_one()
    await _make_due(reminder.id)
    await engine.run_due()
    assert (await _reload(reminder.id)).status is ActionStatus.SUCCEEDED
    async with SessionLocal() as s:
        appt = (await s.execute(select(Appointment).where(Appointment.tenant_id == tenant.id))).scalar_one()
    assert appt.reminder_sent_at is not None
