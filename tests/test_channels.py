"""Channel adapters: WhatsApp / Messenger webhook contracts, web widget (HTTP + WebSocket), REST API."""

import asyncio
import hashlib
import hmac
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.channels import inbound, meta
from app.channels.delivery import with_sources
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.pubsub import publish, widget_channel
from app.core.rbac import Role
from app.models import ApiKey, ApiKeyKind, Channel, Contact, Conversation, Message, MessageRole, Tenant
from tests.conftest import auth_headers, make_agent, make_tenant, make_user

APP_SECRET = "meta-app-secret"


def _sign(body: bytes, secret: str = APP_SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def wa_payload(
    text: str = "hello", msg_id: str = "wamid.1", phone_number_id: str = "PNID1", kind: str = "text"
):
    message = {"from": "919876543210", "id": msg_id, "timestamp": "1730000000", "type": kind}
    if kind == "text":
        message["text"] = {"body": text}
    elif kind == "interactive":
        message["interactive"] = {"type": "button_reply", "button_reply": {"id": "qr0", "title": text}}
    elif kind == "image":
        message["image"] = {"id": "media1", "mime_type": "image/jpeg"}
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550001111",
                                "phone_number_id": phone_number_id,
                            },
                            "contacts": [{"profile": {"name": "Ravi"}, "wa_id": "919876543210"}],
                            "messages": [message],
                        },
                    }
                ],
            }
        ],
    }


@pytest.fixture
def meta_config(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    from pydantic import SecretStr

    for name in ("whatsapp_app_secret", "messenger_app_secret"):
        monkeypatch.setattr(settings, name, SecretStr(APP_SECRET))
    monkeypatch.setattr(settings, "whatsapp_verify_token", SecretStr("verify-me"))
    monkeypatch.setattr(settings, "messenger_verify_token", SecretStr("verify-me"))
    monkeypatch.setattr(settings, "whatsapp_access_token", SecretStr("wa-token"))
    monkeypatch.setattr(settings, "messenger_page_access_token", SecretStr("page-token"))
    sent: list[dict] = []

    async def fake_post(url, json_body, *, token=None, params=None):  # type: ignore[no-untyped-def]
        sent.append({"url": url, "body": json_body, "token": token})
        return {"messages": [{"id": "wamid.out"}]}

    monkeypatch.setattr(meta, "_post", fake_post)
    return sent


async def _tenant_with(**settings_patch: str) -> Tenant:
    tenant = await make_tenant()
    await make_agent(tenant, "clinic_appointment_booker")
    async with SessionLocal() as s:
        t = await s.get(Tenant, tenant.id)
        t.settings = {**t.settings, **settings_patch}
        await s.commit()
    return tenant


# ---------------------------------------------------------------- WhatsApp
async def test_whatsapp_verification_handshake(client: AsyncClient, meta_config) -> None:  # type: ignore[no-untyped-def]
    ok = await client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "12345"},
    )
    assert ok.status_code == 200 and ok.text == "12345"
    bad = await client.get(
        "/webhooks/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "12345"},
    )
    assert bad.status_code == 403


async def test_whatsapp_rejects_unsigned_and_forged_payloads(client: AsyncClient, meta_config) -> None:  # type: ignore[no-untyped-def]
    body = json.dumps(wa_payload()).encode()
    assert (await client.post("/webhooks/whatsapp", content=body)).status_code == 401
    forged = await client.post(
        "/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": _sign(body, "x")}
    )
    assert forged.status_code == 401


async def test_whatsapp_message_round_trip_and_retry_dedupe(client: AsyncClient, meta_config) -> None:  # type: ignore[no-untyped-def]
    tenant = await _tenant_with(whatsapp_phone_number_id="PNID-RT")
    body = json.dumps(wa_payload("hi there", "wamid.rt1", "PNID-RT")).encode()
    resp = await client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": _sign(body)})
    assert resp.json() == {"status": "ok", "received": 1}
    await inbound.drain()

    async with SessionLocal() as s:
        conv = (await s.execute(select(Conversation).where(Conversation.tenant_id == tenant.id))).scalar_one()
        contact = await s.get(Contact, conv.contact_id)
    assert conv.channel is Channel.WHATSAPP and conv.session_id == "919876543210"
    assert contact.phone == "+919876543210" and contact.name == "Ravi"
    assert len(meta_config) == 1
    out = meta_config[0]
    assert out["url"].endswith("/PNID-RT/messages") and out["token"] == "wa-token"
    assert out["body"]["to"] == "919876543210" and out["body"]["messaging_product"] == "whatsapp"

    # Meta retries the same delivery: processed once only.
    await client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": _sign(body)})
    await inbound.drain()
    async with SessionLocal() as s:
        count = len(
            (
                await s.execute(
                    select(Message).where(
                        Message.conversation_id == conv.id, Message.role == MessageRole.USER
                    )
                )
            )
            .scalars()
            .all()
        )
    assert count == 1 and len(meta_config) == 1


async def test_whatsapp_quick_replies_become_buttons_and_media_gets_notice(
    client: AsyncClient, meta_config
) -> None:  # type: ignore[no-untyped-def]
    await _tenant_with(whatsapp_phone_number_id="PNID-QR")
    body = json.dumps(wa_payload("Hello", "wamid.qr1", "PNID-QR")).encode()  # greeting -> quick replies
    await client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": _sign(body)})
    image = json.dumps(wa_payload("", "wamid.img", "PNID-QR", kind="image")).encode()
    await client.post("/webhooks/whatsapp", content=image, headers={"X-Hub-Signature-256": _sign(image)})
    await inbound.drain()
    kinds = {m["body"]["type"] for m in meta_config}
    assert "interactive" in kinds
    buttons = next(m for m in meta_config if m["body"]["type"] == "interactive")["body"]["interactive"][
        "action"
    ]
    assert all(len(b["reply"]["title"]) <= 20 for b in buttons["buttons"]) and len(buttons["buttons"]) <= 3
    assert any("only read text" in m["body"].get("text", {}).get("body", "") for m in meta_config)


async def test_whatsapp_unmapped_number_is_ignored_unless_default_tenant(
    client: AsyncClient,
    meta_config,
    monkeypatch: pytest.MonkeyPatch,  # type: ignore[no-untyped-def]
) -> None:
    body = json.dumps(wa_payload("hi", "wamid.un1", "UNKNOWN")).encode()
    await client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": _sign(body)})
    await inbound.drain()
    assert meta_config == []
    tenant = await _tenant_with()
    monkeypatch.setattr(settings, "channels_default_tenant", tenant.slug)
    body = json.dumps(wa_payload("hi", "wamid.un2", "UNKNOWN")).encode()
    await client.post("/webhooks/whatsapp", content=body, headers={"X-Hub-Signature-256": _sign(body)})
    await inbound.drain()
    assert len(meta_config) == 1


def test_whatsapp_parser_contract() -> None:
    assert (
        meta.parse_whatsapp({"entry": [{"changes": [{"field": "messages", "value": {"statuses": [{}]}}]}]})
        == []
    )
    parsed = meta.parse_whatsapp(wa_payload("Book a appointment", kind="interactive"))
    assert parsed[0].text == "Book a appointment" and parsed[0].sender_name == "Ravi"
    image = meta.parse_whatsapp(wa_payload(kind="image"))
    assert image[0].text == "" and image[0].attachments == [{"type": "image"}]


# ---------------------------------------------------------------- Messenger
def messenger_payload(text: str = "hi", page_id: str = "PAGE1", mid: str = "m_1", echo: bool = False):
    message: dict = {"mid": mid, "text": text}
    if echo:
        message["is_echo"] = True
    return {
        "object": "page",
        "entry": [
            {
                "id": page_id,
                "time": 1,
                "messaging": [
                    {
                        "sender": {"id": "PSID-9"},
                        "recipient": {"id": page_id},
                        "timestamp": 1,
                        "message": message,
                    }
                ],
            }
        ],
    }


async def test_messenger_round_trip_and_echo_ignored(client: AsyncClient, meta_config) -> None:  # type: ignore[no-untyped-def]
    tenant = await _tenant_with(messenger_page_id="PAGE-RT")
    ok = await client.get(
        "/webhooks/messenger",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "abc"},
    )
    assert ok.text == "abc"
    echo = json.dumps(messenger_payload("ignored", "PAGE-RT", "m_e", echo=True)).encode()
    assert (
        await client.post("/webhooks/messenger", content=echo, headers={"X-Hub-Signature-256": _sign(echo)})
    ).json()["received"] == 0
    body = json.dumps(messenger_payload("hello", "PAGE-RT", "m_2")).encode()
    await client.post("/webhooks/messenger", content=body, headers={"X-Hub-Signature-256": _sign(body)})
    await inbound.drain()
    async with SessionLocal() as s:
        conv = (await s.execute(select(Conversation).where(Conversation.tenant_id == tenant.id))).scalar_one()
    assert conv.channel is Channel.MESSENGER and conv.session_id == "PSID-9"
    sent = meta_config[0]
    assert sent["url"].endswith("/me/messages") and sent["body"]["recipient"] == {"id": "PSID-9"}
    assert sent["body"]["messaging_type"] == "RESPONSE" and "quick_replies" in sent["body"]["message"]


def test_sources_are_appended_for_plain_text_channels() -> None:
    text = with_sources("Whitening costs Rs 8,000 [1].", [{"title": "Clinic FAQ"}, {"title": "Clinic FAQ"}])
    assert text.endswith("Sources: [1] Clinic FAQ")


# ---------------------------------------------------------------- web widget
async def _public_key(tenant: Tenant) -> str:
    async with SessionLocal() as s:
        return (
            await s.execute(
                select(ApiKey.public_key).where(
                    ApiKey.tenant_id == tenant.id, ApiKey.kind == ApiKeyKind.PUBLIC
                )
            )
        ).scalar_one()


async def test_widget_config_session_message_and_history(client: AsyncClient) -> None:
    tenant = await _tenant_with()
    key = await _public_key(tenant)
    assert (await client.get("/api/v1/widget/config", params={"key": "pk_nope"})).status_code == 401
    cfg = (await client.get("/api/v1/widget/config", params={"key": key})).json()
    assert cfg["branding"]["primary_color"] and "Welcome" in cfg["greeting"] and cfg["quick_replies"]
    session = cfg["session"]
    same = (
        await client.get(
            "/api/v1/widget/config",
            params={"key": key, "session_id": session["id"], "token": session["token"]},
        )
    ).json()
    assert same["session"] == session  # valid session is kept

    forged = await client.post(
        "/api/v1/widget/messages",
        json={"key": key, "session_id": session["id"], "token": "0" * 40, "text": "hi"},
    )
    assert forged.status_code == 401
    resp = await client.post(
        "/api/v1/widget/messages",
        json={
            "key": key,
            "session_id": session["id"],
            "token": session["token"],
            "text": "hello",
            "metadata": {"utm_source": "instagram", "page_url": "https://clinic.example/offers"},
        },
    )
    body = resp.json()
    assert resp.status_code == 200 and body["text"] and "debug" not in body
    history = (
        await client.get(
            "/api/v1/widget/history",
            params={"key": key, "session_id": session["id"], "token": session["token"]},
        )
    ).json()
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"]
    async with SessionLocal() as s:
        conv = (await s.execute(select(Conversation).where(Conversation.tenant_id == tenant.id))).scalar_one()
    assert conv.meta["utm_source"] == "instagram"


async def test_widget_origin_allowlist_and_rate_limit(client: AsyncClient) -> None:
    tenant = await _tenant_with()
    async with SessionLocal() as s:
        t = await s.get(Tenant, tenant.id)
        t.settings = {**t.settings, "widget_allowed_origins": ["https://clinic.example"]}
        await s.commit()
    key = await _public_key(tenant)
    blocked = await client.get(
        "/api/v1/widget/config", params={"key": key}, headers={"Origin": "https://evil.example"}
    )
    assert blocked.status_code == 403
    cfg = (
        await client.get(
            "/api/v1/widget/config", params={"key": key}, headers={"Origin": "https://clinic.example"}
        )
    ).json()
    s_id, tok = cfg["session"]["id"], cfg["session"]["token"]
    codes = []
    for _ in range(22):
        r = await client.post(
            "/api/v1/widget/messages",
            headers={"Origin": "https://clinic.example"},
            json={"key": key, "session_id": s_id, "token": tok, "text": "hello"},
        )
        codes.append(r.status_code)
    assert 429 in codes  # per-session limit (20/min)


async def test_widget_websocket_live_messages() -> None:
    from httpx import AsyncClient as Client
    from httpx_ws import aconnect_ws
    from httpx_ws.transport import ASGIWebSocketTransport

    from app.channels.widget_auth import new_session
    from app.main import app

    tenant = await _tenant_with()
    key = await _public_key(tenant)
    session_id, token = new_session(tenant.id)
    async with Client(transport=ASGIWebSocketTransport(app), base_url="http://test") as c:
        with pytest.raises(Exception):
            async with aconnect_ws(
                f"http://test/api/v1/widget/ws?key={key}&session_id={session_id}&token=bad", c
            ):
                pass
        url = f"http://test/api/v1/widget/ws?key={key}&session_id={session_id}&token={token}"
        async with aconnect_ws(url, c) as ws:
            await ws.send_json({"type": "ping"})
            assert (await ws.receive_json(timeout=5))["type"] == "pong"
            await ws.send_json({"type": "message", "text": "hello"})
            events = [await ws.receive_json(timeout=10) for _ in range(2)]
            assert events[0]["type"] == "typing"
            assert events[1]["type"] == "message" and events[1]["role"] == "assistant" and events[1]["text"]
            # A human agent's reply published for this visitor arrives live.
            await asyncio.sleep(0.1)
            await publish(
                widget_channel(tenant.id, session_id),
                {"type": "message", "role": "human_agent", "text": "Hi, this is Asha from the clinic"},
            )
            live = await ws.receive_json(timeout=5)
            assert live["role"] == "human_agent" and "Asha" in live["text"]


async def test_widget_js_demo_page_and_cors(client: AsyncClient) -> None:
    tenant = await _tenant_with()
    js = await client.get("/widget.js")
    assert js.status_code == 200 and "attachShadow" in js.text and "javascript" in js.headers["content-type"]
    demo = await client.get(f"/demo/{tenant.slug}")
    assert demo.status_code == 200 and 'data-key="pk_' in demo.text
    assert (await client.get("/demo/no-such-tenant")).status_code == 404

    pre = {
        "Origin": "https://any-customer-site.example",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "content-type",
    }
    public = await client.options("/api/v1/widget/messages", headers=pre)
    assert public.headers.get("access-control-allow-origin") == "*"
    private = await client.options("/api/v1/users", headers=pre)
    assert private.headers.get("access-control-allow-origin") != "*"
    assert "any-customer-site" not in private.headers.get("access-control-allow-origin", "")


# ---------------------------------------------------------------- REST channel
async def test_rest_channel_requires_secret_key_and_is_isolated(client: AsyncClient) -> None:
    tenant = await _tenant_with()
    headers = await auth_headers(client, await make_user(tenant, Role.TENANT_ADMIN))
    secret = (
        await client.post("/api/v1/api-keys", json={"name": "crm", "kind": "secret"}, headers=headers)
    ).json()["key"]
    public = await _public_key(tenant)
    body = {
        "session_id": "crm-user-42",
        "text": "hello",
        "contact": {"name": "Neha", "email": "neha@example.org"},
        "metadata": {"utm_campaign": "diwali"},
    }
    assert (await client.post("/api/v1/channels/rest/messages", json=body)).status_code == 401
    assert (
        await client.post("/api/v1/channels/rest/messages", json=body, headers={"X-API-Key": public})
    ).status_code == 401
    resp = await client.post("/api/v1/channels/rest/messages", json=body, headers={"X-API-Key": secret})
    assert resp.status_code == 200 and resp.json()["text"] and resp.json()["debug"] == {}
    history = await client.get(
        "/api/v1/channels/rest/conversations/crm-user-42/messages", headers={"X-API-Key": secret}
    )
    assert [m["role"] for m in history.json()] == ["user", "assistant"]

    other = await _tenant_with()
    other_headers = await auth_headers(client, await make_user(other, Role.TENANT_ADMIN))
    other_secret = (
        await client.post("/api/v1/api-keys", json={"name": "x", "kind": "secret"}, headers=other_headers)
    ).json()["key"]
    leaked = await client.get(
        "/api/v1/channels/rest/conversations/crm-user-42/messages", headers={"X-API-Key": other_secret}
    )
    assert leaked.status_code == 404
