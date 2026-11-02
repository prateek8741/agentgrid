"""Human handover inbox: live replies, AI silence, hand back, assignment, isolation, live feed."""

import asyncio
import json
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.channels import meta
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.pubsub import subscribe, widget_channel
from app.core.rbac import Role
from app.models import Conversation, ConversationStatus, Message, MessageRole
from app.workers.events import get_event_bus
from tests.conftest import auth_headers, chat, make_agent, make_tenant, make_user


async def _collect(channel: str, into: list[dict], ready: asyncio.Event) -> None:
    gen = subscribe(channel)
    ready.set()
    async for event in gen:
        if event.get("type") != "_idle":
            into.append(event)


async def _setup(client: AsyncClient):  # type: ignore[no-untyped-def]
    tenant = await make_tenant()
    await make_agent(tenant, "customer_support_faq")
    agent_user = await make_user(tenant, Role.AGENT)
    return tenant, agent_user, await auth_headers(client, agent_user)


async def test_full_handover_loop_on_web_widget(client: AsyncClient) -> None:
    tenant, agent_user, headers = await _setup(client)
    session = "w_visitor_handover_01"
    r = await chat(tenant, "I want to talk to a human", session_id=session)
    assert r.handed_over

    inbox = (await client.get("/api/v1/inbox/conversations", headers=headers)).json()
    assert inbox["counts"]["handed_over"] == 1
    item = inbox["items"][0]
    assert item["handover_reason"] == "customer_request" and item["last_message"]["role"] == "assistant"
    conv_id = item["id"]

    widget_events: list[dict] = []
    ready = asyncio.Event()
    listener = asyncio.create_task(_collect(widget_channel(tenant.id, session), widget_events, ready))
    await ready.wait()
    await asyncio.sleep(0.05)
    sent = await client.post(
        f"/api/v1/inbox/conversations/{conv_id}/reply",
        json={"text": "Hi, I'm Asha from support. How can I help?"},
        headers=headers,
    )
    assert sent.status_code == 200 and sent.json()["role"] == "human_agent"
    for _ in range(40):
        if widget_events:
            break
        await asyncio.sleep(0.05)
    listener.cancel()
    assert widget_events[0]["role"] == "human_agent" and "Asha" in widget_events[0]["text"]

    # While a human owns the conversation the AI stays silent.
    quiet = await chat(tenant, "My invoice shows a double charge", session_id=session)
    assert quiet.text is None and quiet.handed_over
    async with SessionLocal() as s:
        conv = await s.get(Conversation, uuid.UUID(conv_id))
        assert conv.assigned_user_id == agent_user.id
        roles = [
            m.role
            for m in (
                await s.execute(
                    select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at)
                )
            ).scalars()
        ]
    assert roles.count(MessageRole.HUMAN_AGENT) == 1 and roles[-1] is MessageRole.USER

    back = await client.post(f"/api/v1/inbox/conversations/{conv_id}/handback", headers=headers)
    assert back.json()["status"] == "active" and back.json()["assigned_user_id"] is None
    again = await chat(tenant, "hello", session_id=session)
    assert again.text and not again.handed_over  # AI is answering again

    closed = await client.post(f"/api/v1/inbox/conversations/{conv_id}/close", headers=headers)
    assert closed.json()["status"] == "closed"
    reopened = await chat(tenant, "one more question please", session_id=session)
    assert reopened.status == "active"


async def test_takeover_and_assignment(client: AsyncClient) -> None:
    tenant, agent_user, headers = await _setup(client)
    teammate = await make_user(tenant, Role.AGENT)
    r = await chat(tenant, "hello", session_id="w_takeover_session_1")
    conv_id = str(r.conversation_id)
    taken = await client.post(f"/api/v1/inbox/conversations/{conv_id}/takeover", headers=headers)
    assert taken.json()["status"] == "handed_over" and taken.json()["assigned_user_id"] == str(agent_user.id)
    moved = await client.post(
        f"/api/v1/inbox/conversations/{conv_id}/assign", json={"user_id": str(teammate.id)}, headers=headers
    )
    assert moved.json()["assigned_user_id"] == str(teammate.id)
    mine = (await client.get("/api/v1/inbox/conversations?mine=true", headers=headers)).json()
    assert mine["total"] == 0

    outsider = await make_user(await make_tenant(), Role.AGENT)
    bad = await client.post(
        f"/api/v1/inbox/conversations/{conv_id}/assign", json={"user_id": str(outsider.id)}, headers=headers
    )
    assert bad.status_code == 404
    assert (
        await client.post(f"/api/v1/inbox/conversations/{conv_id}/handback", headers=headers)
    ).status_code == 200
    assert (
        await client.post(f"/api/v1/inbox/conversations/{conv_id}/handback", headers=headers)
    ).status_code == 409


async def test_inbox_rbac_and_tenant_isolation(client: AsyncClient) -> None:
    tenant, _, _ = await _setup(client)
    r = await chat(tenant, "talk to a human", session_id="w_rbac_session_0001")
    viewer = await auth_headers(client, await make_user(tenant, Role.VIEWER))
    assert (await client.get("/api/v1/inbox/conversations", headers=viewer)).status_code == 403
    assert (
        await client.post(
            f"/api/v1/inbox/conversations/{r.conversation_id}/reply", json={"text": "x"}, headers=viewer
        )
    ).status_code == 403
    _, _, other = await _setup(client)
    assert (
        await client.post(
            f"/api/v1/inbox/conversations/{r.conversation_id}/reply", json={"text": "x"}, headers=other
        )
    ).status_code == 404
    assert (await client.get("/api/v1/inbox/conversations", headers=other)).json()["total"] == 0


async def test_inbox_websocket_live_feed(client: AsyncClient) -> None:
    from httpx import AsyncClient as Client
    from httpx_ws import aconnect_ws
    from httpx_ws.transport import ASGIWebSocketTransport

    from app.main import app

    tenant, _, headers = await _setup(client)
    token = headers["Authorization"].split()[1]
    async with Client(transport=ASGIWebSocketTransport(app), base_url="http://test") as c:
        with pytest.raises(Exception):
            async with aconnect_ws("http://test/api/v1/inbox/ws?token=garbage", c):
                pass
        async with aconnect_ws(f"http://test/api/v1/inbox/ws?token={token}", c) as ws:
            await asyncio.sleep(0.1)
            await chat(tenant, "I want to talk to a human right now", session_id="w_live_feed_000001")
            seen = []
            for _ in range(6):
                event = await ws.receive_json(timeout=5)
                seen.append(event["type"])
                if "handover.requested" in seen:
                    break
            assert "message.received" in seen and "handover.requested" in seen


async def test_human_reply_is_delivered_to_whatsapp(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic import SecretStr

    from app.agents.orchestrator import get_orchestrator
    from app.channels.schema import ContactInfo, InboundMessage
    from app.models import Channel

    monkeypatch.setattr(settings, "whatsapp_access_token", SecretStr("wa-token"))
    sent: list[dict] = []

    async def fake_post(url, json_body, *, token=None, params=None):  # type: ignore[no-untyped-def]
        sent.append({"url": url, "body": json_body})
        return {}

    monkeypatch.setattr(meta, "_post", fake_post)
    tenant, _, headers = await _setup(client)
    async with SessionLocal() as s:
        reply = await get_orchestrator().handle(
            s,
            InboundMessage(
                tenant_id=tenant.id,
                channel=Channel.WHATSAPP,
                session_id="919800000001",
                text="talk to a human",
                contact=ContactInfo(external_id="919800000001"),
                metadata={"extra": {"account_id": "PNID-HUMAN"}},
            ),
        )
    await get_event_bus().drain()
    resp = await client.post(
        f"/api/v1/inbox/conversations/{reply.conversation_id}/reply",
        json={"text": "Hello from our team!"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert sent and sent[-1]["url"].endswith("/PNID-HUMAN/messages")
    assert (
        sent[-1]["body"]["text"]["body"] == "Hello from our team!"
        and sent[-1]["body"]["to"] == "919800000001"
    )
    async with SessionLocal() as s:
        conv = await s.get(Conversation, reply.conversation_id)
    assert conv.status is ConversationStatus.HANDED_OVER
    assert json.dumps(conv.meta).count("PNID-HUMAN") == 1
