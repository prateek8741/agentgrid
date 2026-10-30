from httpx import AsyncClient

from app.core.rbac import Role
from tests.conftest import auth_headers, make_tenant, make_user


async def test_branding_update_and_validation(client: AsyncClient) -> None:
    tenant = await make_tenant()
    headers = await auth_headers(client, await make_user(tenant, Role.TENANT_ADMIN))
    body = {
        "brand_name": "Sunrise",
        "primary_color": "#0EA5E9",
        "widget_title": "Ask Sunrise",
        "launcher_position": "left",
    }
    resp = await client.put("/api/v1/tenant/branding", json=body, headers=headers)
    assert resp.status_code == 200
    assert (await client.get("/api/v1/tenant/branding", headers=headers)).json()[
        "widget_title"
    ] == "Ask Sunrise"

    bad = await client.put(
        "/api/v1/tenant/branding", json={"primary_color": "red; background:url(x)"}, headers=headers
    )
    assert bad.status_code == 422
    bad_logo = await client.put(
        "/api/v1/tenant/branding", json={"logo_url": "javascript:alert(1)"}, headers=headers
    )
    assert bad_logo.status_code == 422


async def test_settings_update(client: AsyncClient) -> None:
    tenant = await make_tenant()
    headers = await auth_headers(client, await make_user(tenant, Role.TENANT_ADMIN))
    resp = await client.patch(
        "/api/v1/tenant/settings",
        json={
            "retention_days": 30,
            "blocked_terms": ["  Casino ", "casino", "crypto"],
            "timezone": "Asia/Kolkata",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    settings = resp.json()["settings"]
    assert settings["retention_days"] == 30
    assert settings["blocked_terms"] == ["casino", "crypto"]
    bad = await client.patch("/api/v1/tenant/settings", json={"timezone": "Mars/Base"}, headers=headers)
    assert bad.status_code == 422


async def test_agent_crud_and_single_default(client: AsyncClient) -> None:
    tenant = await make_tenant()
    headers = await auth_headers(client, await make_user(tenant, Role.TENANT_ADMIN))
    agents = (await client.get("/api/v1/agents", headers=headers)).json()
    assert len(agents) == 1 and agents[0]["is_default"]

    created = await client.post(
        "/api/v1/agents",
        json={
            "name": "Sales Bot",
            "persona": "You sell.",
            "is_default": True,
            "enabled_tools": ["capture_lead"],
        },
        headers=headers,
    )
    assert created.status_code == 201
    agents = (await client.get("/api/v1/agents", headers=headers)).json()
    assert [a["name"] for a in agents if a["is_default"]] == ["Sales Bot"]

    old_id = next(a["id"] for a in agents if a["name"] == "Assistant")
    assert (await client.delete(f"/api/v1/agents/{old_id}", headers=headers)).status_code == 204
    cannot = await client.delete(f"/api/v1/agents/{created.json()['id']}", headers=headers)
    assert cannot.status_code == 409


async def test_audit_log_records_admin_actions(client: AsyncClient) -> None:
    tenant = await make_tenant()
    headers = await auth_headers(client, await make_user(tenant, Role.TENANT_ADMIN))
    await client.post("/api/v1/agents", json={"name": "Audited"}, headers=headers)
    await client.post("/api/v1/api-keys", json={"name": "server", "kind": "secret"}, headers=headers)
    logs = (await client.get("/api/v1/audit-logs", headers=headers)).json()
    actions = {item["action"] for item in logs["items"]}
    assert {"auth.login", "agent.create", "api_key.create"} <= actions
    assert all(item["tenant_id"] == str(tenant.id) for item in logs["items"])
    filtered = (await client.get("/api/v1/audit-logs?action=agent.", headers=headers)).json()
    assert {i["action"] for i in filtered["items"]} == {"agent.create"}


async def test_tenant_admin_cannot_raise_own_limits(client: AsyncClient) -> None:
    tenant = await make_tenant()
    headers = await auth_headers(client, await make_user(tenant, Role.TENANT_ADMIN))
    for body in ({"rate_limit_per_minute": 10000}, {"monthly_token_quota": 1_000_000_000}):
        resp = await client.patch("/api/v1/tenant/settings", json=body, headers=headers)
        assert resp.status_code == 403, body


async def test_settings_patch_is_partial_and_preserves_owner_limits(client: AsyncClient) -> None:
    from app.models import TenantKind

    agency = await make_tenant(kind=TenantKind.AGENCY)
    child = await make_tenant(parent_id=agency.id)
    agency_headers = await auth_headers(client, await make_user(agency, Role.TENANT_ADMIN))
    resp = await client.patch(
        f"/api/v1/tenants/{child.id}",
        json={"settings": {"monthly_token_quota": 123456}},
        headers=agency_headers,
    )
    assert resp.status_code == 200, resp.text  # the owning agency may set limits

    child_headers = await auth_headers(client, await make_user(child, Role.TENANT_ADMIN))
    resp = await client.patch("/api/v1/tenant/settings", json={"retention_days": 90}, headers=child_headers)
    assert resp.status_code == 200
    settings = resp.json()["settings"]
    assert settings["retention_days"] == 90
    assert settings["monthly_token_quota"] == 123456  # not reset to the default


async def test_agent_patch_rejects_explicit_nulls(client: AsyncClient) -> None:
    tenant = await make_tenant()
    headers = await auth_headers(client, await make_user(tenant, Role.TENANT_ADMIN))
    agent_id = (await client.get("/api/v1/agents", headers=headers)).json()[0]["id"]
    resp = await client.patch(f"/api/v1/agents/{agent_id}", json={"name": None}, headers=headers)
    assert resp.status_code == 422
    ok = await client.patch(f"/api/v1/agents/{agent_id}", json={"llm_model": None}, headers=headers)
    assert ok.status_code == 200  # null llm_model means "platform default"
