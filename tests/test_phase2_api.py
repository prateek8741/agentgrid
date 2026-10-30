from datetime import date, timedelta

from httpx import AsyncClient
from sqlalchemy import select

from app.agents.llm.scripted import ScriptedCall, nlu_json, reply, tool_call
from app.core.database import SessionLocal
from app.core.rbac import Role
from app.models import PaymentLink, PaymentStatus
from tests.conftest import auth_headers, make_tenant, make_user, use_scripted_llm

CSV = b"""sku,name,price,category,tags,description,in_stock
RUN-1,Swift Runner Shoes,4499,Footwear,running|shoes,Lightweight running shoes,true
RUN-2,Trail Blazer Shoes,6999,Footwear,running|trail,Grippy trail running shoes,true
BAG-1,City Backpack,2499,Bags,backpack|laptop,20L laptop backpack,true
OLD-1,Retro Sneakers,1999,Footwear,shoes,Out of stock sneakers,false
,Missing SKU,10,,,,
"""


async def _admin(client: AsyncClient):  # type: ignore[no-untyped-def]
    tenant = await make_tenant()
    return tenant, await auth_headers(client, await make_user(tenant, Role.TENANT_ADMIN))


async def test_templates_and_create_agent_from_template(client: AsyncClient) -> None:
    tenant, headers = await _admin(client)
    templates = (await client.get("/api/v1/agent-templates", headers=headers)).json()
    assert {t["key"] for t in templates} >= {"real_estate_lead_qualifier", "clinic_appointment_booker"}
    one = templates[0]
    assert one["persona"] and one["intents"] and one["qualification_questions"] is not None

    resp = await client.post(
        "/api/v1/agents/from-template",
        json={"template_key": "clinic_appointment_booker", "make_default": True},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    agent = resp.json()
    assert agent["template_key"] == "clinic_appointment_booker" and agent["is_default"]
    assert tenant.name in agent["persona"] and "{business}" not in agent["greeting"]
    assert "book_appointment" in agent["enabled_tools"]
    missing = await client.post(
        "/api/v1/agents/from-template", json={"template_key": "nope"}, headers=headers
    )
    assert missing.status_code == 404


async def test_playground_chat_with_scripted_llm(client: AsyncClient) -> None:
    _, headers = await _admin(client)
    agent = (
        await client.post(
            "/api/v1/agents/from-template", json={"template_key": "customer_support_faq"}, headers=headers
        )
    ).json()

    def responder(call: ScriptedCall):  # type: ignore[no-untyped-def]
        if call.json_mode:
            return nlu_json("question", 0.92)
        return reply("Our support hours are 9am-6pm IST.")

    use_scripted_llm(responder)
    resp = await client.post(
        f"/api/v1/agents/{agent['id']}/playground",
        json={"message": "When is your support team available?"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text"] == "Our support hours are 9am-6pm IST."
    assert body["intent"] == "question" and body["debug"]["decision"] == "llm"
    session = body["debug"]["session_id"]
    again = await client.post(
        f"/api/v1/agents/{agent['id']}/playground",
        json={"message": "thanks, bye", "session_id": session},
        headers=headers,
    )
    assert again.json()["conversation_id"] == body["conversation_id"]

    usage = (await client.get("/api/v1/usage", headers=headers)).json()
    assert usage["tokens_used"] > 0 and usage["llm_providers"] == ["scripted"]
    convs = (await client.get("/api/v1/conversations", headers=headers)).json()
    assert convs["total"] == 1 and convs["items"][0]["channel"] == "playground"
    messages = (
        await client.get(f"/api/v1/conversations/{body['conversation_id']}/messages", headers=headers)
    ).json()
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]


async def test_leads_api_pipeline_and_isolation(client: AsyncClient) -> None:
    _, headers = await _admin(client)
    created = await client.post(
        "/api/v1/leads",
        json={
            "name": "Kiran",
            "phone": "9876543210",
            "need": "2BHK",
            "budget": "60 lakh",
            "timeline": "next month",
        },
        headers=headers,
    )
    assert created.status_code == 201
    lead = created.json()
    assert (
        lead["temperature"] in ("hot", "warm")
        and lead["score"] > 40
        and lead["budget_amount"] == "6000000.00"
    )
    moved = await client.patch(
        f"/api/v1/leads/{lead['id']}", json={"status": "qualified", "notes": "called"}, headers=headers
    )
    assert moved.json()["status"] == "qualified"
    pipeline = (await client.get("/api/v1/leads/pipeline", headers=headers)).json()
    assert pipeline["qualified"] == 1 and pipeline["new"] == 0
    assert (await client.get("/api/v1/leads?temperature=cold", headers=headers)).json()["total"] == 0

    _, other = await _admin(client)
    assert (await client.get(f"/api/v1/leads/{lead['id']}", headers=other)).status_code == 404
    assert (
        await client.patch(f"/api/v1/leads/{lead['id']}", json={"status": "lost"}, headers=other)
    ).status_code == 404


async def test_slots_and_appointments_api(client: AsyncClient) -> None:
    _, headers = await _admin(client)
    start = date.today() + timedelta(days=1)
    resp = await client.post(
        "/api/v1/slots/generate",
        json={
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=1)).isoformat(),
            "weekdays": list(range(7)),
            "day_start": "09:00",
            "day_end": "11:00",
            "slot_minutes": 60,
        },
        headers=headers,
    )
    assert resp.json() == {"created": 4}
    again = await client.post(
        "/api/v1/slots/generate",
        json={
            "start_date": start.isoformat(),
            "end_date": (start + timedelta(days=1)).isoformat(),
            "weekdays": list(range(7)),
            "day_start": "09:00",
            "day_end": "11:00",
            "slot_minutes": 60,
        },
        headers=headers,
    )
    assert again.json() == {"created": 0}  # idempotent
    bad = await client.post(
        "/api/v1/slots/generate",
        json={"start_date": start.isoformat(), "end_date": (start - timedelta(days=3)).isoformat()},
        headers=headers,
    )
    assert bad.status_code == 422
    slots = (await client.get(f"/api/v1/slots?start={start.isoformat()}&days=2", headers=headers)).json()
    assert len(slots) == 4
    assert (await client.delete(f"/api/v1/slots/{slots[0]['id']}", headers=headers)).status_code == 204
    assert (await client.get("/api/v1/appointments?upcoming=true", headers=headers)).json()["total"] == 0


async def test_product_csv_import_and_recommendation_tool(client: AsyncClient) -> None:
    _, headers = await _admin(client)
    resp = await client.post(
        "/api/v1/products/import", files={"file": ("catalog.csv", CSV, "text/csv")}, headers=headers
    )
    body = resp.json()
    assert body["created"] == 4 and len(body["errors"]) == 1
    reimport = await client.post(
        "/api/v1/products/import", files={"file": ("c.csv", CSV, "text/csv")}, headers=headers
    )
    assert reimport.json()["updated"] == 4
    assert (await client.get("/api/v1/products", headers=headers)).json()["total"] == 4

    agent = (
        await client.post(
            "/api/v1/agents/from-template",
            json={"template_key": "ecommerce_product_recommender"},
            headers=headers,
        )
    ).json()
    tool_results: list[str] = []

    def responder(call: ScriptedCall):  # type: ignore[no-untyped-def]
        if call.json_mode:
            return nlu_json("product_search", 0.9)
        tools = [m for m in call.messages if m["role"] == "tool"]
        if not tools:
            return tool_call("recommend_products", {"query": "running shoes", "max_price": 5000})
        tool_results.append(tools[-1]["content"])
        return reply("I recommend the Swift Runner Shoes at 4499 INR.")

    use_scripted_llm(responder)
    resp = await client.post(
        f"/api/v1/agents/{agent['id']}/playground",
        json={"message": "suggest running shoes under 5000"},
        headers=headers,
    )
    assert "Swift Runner" in resp.json()["text"]
    assert "Swift Runner" in tool_results[0] and "Trail Blazer" not in tool_results[0]  # price filter
    assert "Retro" not in tool_results[0]  # out of stock excluded


async def test_payment_link_tool_and_mock_payment_page(client: AsyncClient) -> None:
    tenant, headers = await _admin(client)
    agent = (
        await client.post(
            "/api/v1/agents/from-template",
            json={"template_key": "ecommerce_product_recommender"},
            headers=headers,
        )
    ).json()

    def responder(call: ScriptedCall):  # type: ignore[no-untyped-def]
        if call.json_mode:
            return nlu_json("payment", 0.9)
        if not any(m["role"] == "tool" for m in call.messages):
            return tool_call("create_payment_link", {"amount": 4499, "description": "Swift Runner Shoes"})
        return reply("Here is your payment link.")

    use_scripted_llm(responder)
    await client.post(
        f"/api/v1/agents/{agent['id']}/playground",
        json={"message": "send me a payment link"},
        headers=headers,
    )
    async with SessionLocal() as s:
        link = (await s.execute(select(PaymentLink).where(PaymentLink.tenant_id == tenant.id))).scalar_one()
    assert link.provider == "mock" and link.url.endswith(str(link.id))
    page = await client.get(f"/pay/mock/{link.id}")
    assert page.status_code == 200 and "4499.00 INR" in page.text
    paid = await client.post(f"/pay/mock/{link.id}/complete")
    assert "Payment received" in paid.text
    async with SessionLocal() as s:
        assert (await s.get(PaymentLink, link.id)).status is PaymentStatus.PAID
    assert (await client.post(f"/pay/mock/{link.id}/complete")).status_code == 409


async def test_ticket_tool_and_tickets_api(client: AsyncClient) -> None:
    _, headers = await _admin(client)
    agent = (
        await client.post(
            "/api/v1/agents/from-template", json={"template_key": "customer_support_faq"}, headers=headers
        )
    ).json()

    def responder(call: ScriptedCall):  # type: ignore[no-untyped-def]
        if call.json_mode:
            return nlu_json("complaint", 0.9, -0.2)
        if not any(m["role"] == "tool" for m in call.messages):
            return tool_call(
                "create_ticket",
                {"subject": "Order not delivered", "description": "Order #123 late", "priority": "high"},
            )
        return reply("I've raised a ticket for you.")

    use_scripted_llm(responder)
    await client.post(
        f"/api/v1/agents/{agent['id']}/playground",
        json={"message": "my order #123 is very late"},
        headers=headers,
    )
    tickets = (await client.get("/api/v1/tickets", headers=headers)).json()
    assert tickets["total"] == 1 and tickets["items"][0]["reference"].startswith("TKT-")
    ticket_id = tickets["items"][0]["id"]
    updated = await client.patch(f"/api/v1/tickets/{ticket_id}", json={"status": "resolved"}, headers=headers)
    assert updated.json()["status"] == "resolved"


async def test_action_log_api_and_retry(client: AsyncClient) -> None:
    tenant, headers = await _admin(client)
    from app.models import ActionLog, ActionStatus

    async with SessionLocal() as s:
        failed = ActionLog(
            tenant_id=tenant.id,
            action_type="notify.team",
            status=ActionStatus.FAILED,
            attempts=5,
            max_attempts=5,
            payload={"subject": "x", "body": "y"},
            last_error="boom",
        )
        s.add(failed)
        await s.commit()
        await s.refresh(failed)
    listed = (await client.get("/api/v1/actions?status=failed", headers=headers)).json()
    assert listed["total"] == 1
    retried = await client.post(f"/api/v1/actions/{failed.id}/retry", headers=headers)
    assert retried.json()["status"] == "pending" and retried.json()["max_attempts"] == 8
    assert (await client.post(f"/api/v1/actions/{failed.id}/retry", headers=headers)).status_code == 409


async def test_viewer_cannot_mutate_phase2_resources(client: AsyncClient) -> None:
    tenant = await make_tenant()
    viewer = await auth_headers(client, await make_user(tenant, Role.VIEWER))
    assert (await client.get("/api/v1/leads", headers=viewer)).status_code == 200
    assert (await client.post("/api/v1/leads", json={"name": "x"}, headers=viewer)).status_code == 403
    assert (await client.get("/api/v1/actions", headers=viewer)).status_code == 403
    assert (
        await client.post(
            "/api/v1/agents/from-template", json={"template_key": "customer_support_faq"}, headers=viewer
        )
    ).status_code == 403
