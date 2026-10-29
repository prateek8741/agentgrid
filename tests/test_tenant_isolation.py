"""Strict tenant isolation: API level and ORM safety net."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.rbac import Role
from app.core.tenancy import TenantIsolationError, set_session_tenant, unscoped
from app.models import Agent, ApiKey, TenantKind, User
from tests.conftest import PASSWORD, auth_headers, bearer, login, make_tenant, make_user


async def test_admin_cannot_read_other_tenants_resources(client: AsyncClient) -> None:
    tenant_a, tenant_b = await make_tenant(), await make_tenant()
    admin_a = await make_user(tenant_a, Role.TENANT_ADMIN)
    user_b = await make_user(tenant_b, Role.AGENT)
    headers = await auth_headers(client, admin_a)

    users = (await client.get("/api/v1/users", headers=headers)).json()
    assert {u["tenant_id"] for u in users["items"]} == {str(tenant_a.id)}

    assert (await client.get(f"/api/v1/users/{user_b.id}", headers=headers)).status_code == 404
    assert (
        await client.patch(f"/api/v1/users/{user_b.id}", json={"full_name": "pwn"}, headers=headers)
    ).status_code == 404
    assert (await client.delete(f"/api/v1/users/{user_b.id}", headers=headers)).status_code == 404
    assert (await client.get(f"/api/v1/tenants/{tenant_b.id}", headers=headers)).status_code == 404

    async with SessionLocal() as s:
        agent_b = (await s.execute(select(Agent).where(Agent.tenant_id == tenant_b.id))).scalar_one()
        key_b = (await s.execute(select(ApiKey).where(ApiKey.tenant_id == tenant_b.id))).scalar_one()
    assert (await client.get(f"/api/v1/agents/{agent_b.id}", headers=headers)).status_code == 404
    assert (await client.post(f"/api/v1/api-keys/{key_b.id}/revoke", headers=headers)).status_code == 404

    agents = (await client.get("/api/v1/agents", headers=headers)).json()
    assert all(a["tenant_id"] == str(tenant_a.id) for a in agents)


async def test_tenant_listing_only_shows_own_tenant(client: AsyncClient) -> None:
    tenant_a, _ = await make_tenant(), await make_tenant()
    headers = await auth_headers(client, await make_user(tenant_a, Role.TENANT_ADMIN))
    body = (await client.get("/api/v1/tenants", headers=headers)).json()
    assert [t["id"] for t in body["items"]] == [str(tenant_a.id)]


async def test_cannot_switch_into_unrelated_tenant(client: AsyncClient) -> None:
    tenant_a, tenant_b = await make_tenant(), await make_tenant()
    headers = await auth_headers(client, await make_user(tenant_a, Role.TENANT_ADMIN))
    resp = await client.post(
        "/api/v1/auth/switch-tenant", json={"tenant_id": str(tenant_b.id)}, headers=headers
    )
    assert resp.status_code == 403


async def test_orm_guard_filters_reads_automatically() -> None:
    tenant_a, tenant_b = await make_tenant(), await make_tenant()
    await make_user(tenant_a, Role.AGENT)
    await make_user(tenant_b, Role.AGENT)
    async with SessionLocal() as s:
        set_session_tenant(s, tenant_a.id)
        # Deliberately "forget" the tenant filter - the ORM guard still applies it.
        users = (await s.execute(select(User))).scalars().all()
        assert users and {u.tenant_id for u in users} == {tenant_a.id}
        async with unscoped(s):
            all_users = (await s.execute(select(User))).scalars().all()
        assert {tenant_a.id, tenant_b.id} <= {u.tenant_id for u in all_users}


async def test_orm_guard_blocks_cross_tenant_writes() -> None:
    tenant_a, tenant_b = await make_tenant(), await make_tenant()
    async with SessionLocal() as s:
        set_session_tenant(s, tenant_a.id)
        s.add(Agent(tenant_id=tenant_b.id, name="intruder"))
        with pytest.raises(TenantIsolationError):
            await s.flush()
        await s.rollback()


async def test_orm_guard_stamps_tenant_on_new_rows() -> None:
    tenant = await make_tenant()
    async with SessionLocal() as s:
        set_session_tenant(s, tenant.id)
        agent = Agent(name="auto-stamped")
        s.add(agent)
        await s.flush()
        assert agent.tenant_id == tenant.id
        await s.rollback()


# ---------------------------------------------------------------- agencies
async def test_agency_creates_and_switches_into_client(client: AsyncClient) -> None:
    agency = await make_tenant(kind=TenantKind.AGENCY)
    agency_admin = await make_user(agency, Role.TENANT_ADMIN)
    headers = await auth_headers(client, agency_admin)

    created = await client.post(
        "/api/v1/tenants",
        json={
            "name": "Client Bakery",
            "branding": {"widget_title": "Bakery Bot", "primary_color": "#ff6600"},
            "admin": {"email": "baker@example.org", "password": "Bakery1234"},
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    client_tenant = created.json()
    assert client_tenant["parent_id"] == str(agency.id)
    assert client_tenant["kind"] == "business"

    listing = (await client.get("/api/v1/tenants", headers=headers)).json()
    assert {str(agency.id), client_tenant["id"]} <= {t["id"] for t in listing["items"]}

    usage = (await client.get("/api/v1/agency/clients", headers=headers)).json()
    assert usage[0]["name"] == "Client Bakery" and usage[0]["users"] == 1

    switched = await client.post(
        "/api/v1/auth/switch-tenant", json={"tenant_id": client_tenant["id"]}, headers=headers
    )
    assert switched.status_code == 200
    client_headers = bearer(switched.json())
    me = (await client.get("/api/v1/auth/me", headers=client_headers)).json()
    assert me["tenant"]["id"] == client_tenant["id"]
    assert me["branding"]["widget_title"] == "Bakery Bot"
    users = (await client.get("/api/v1/users", headers=client_headers)).json()
    assert [u["email"] for u in users["items"]] == ["baker@example.org"]

    # The client's own admin is sandboxed: it cannot see the agency or siblings.
    baker = await login(client, "baker@example.org", "Bakery1234")
    resp = await client.post(
        "/api/v1/auth/switch-tenant", json={"tenant_id": str(agency.id)}, headers=bearer(baker)
    )
    assert resp.status_code == 403


async def test_agency_cannot_reach_other_agencies_clients(client: AsyncClient) -> None:
    agency_1 = await make_tenant(kind=TenantKind.AGENCY)
    agency_2 = await make_tenant(kind=TenantKind.AGENCY)
    foreign_client = await make_tenant(parent_id=agency_2.id)
    headers = await auth_headers(client, await make_user(agency_1, Role.TENANT_ADMIN))
    resp = await client.post(
        "/api/v1/auth/switch-tenant", json={"tenant_id": str(foreign_client.id)}, headers=headers
    )
    assert resp.status_code == 403
    nested = await client.post(
        "/api/v1/tenants", json={"name": "Hijack", "parent_id": str(agency_2.id)}, headers=headers
    )
    assert nested.status_code == 403


async def test_agency_agent_role_cannot_switch_to_clients(client: AsyncClient) -> None:
    agency = await make_tenant(kind=TenantKind.AGENCY)
    child = await make_tenant(parent_id=agency.id)
    headers = await auth_headers(client, await make_user(agency, Role.AGENT))
    resp = await client.post("/api/v1/auth/switch-tenant", json={"tenant_id": str(child.id)}, headers=headers)
    assert resp.status_code == 403


async def test_super_admin_can_manage_any_tenant(client: AsyncClient) -> None:
    platform = await make_tenant(kind=TenantKind.PLATFORM)
    target = await make_tenant()
    headers = await auth_headers(client, await make_user(platform, Role.SUPER_ADMIN))
    resp = await client.patch(f"/api/v1/tenants/{target.id}", json={"status": "suspended"}, headers=headers)
    assert resp.status_code == 200 and resp.json()["status"] == "suspended"

    # Users of a suspended tenant are locked out.
    blocked_user = await make_user(target, Role.TENANT_ADMIN)
    resp = await client.post("/api/v1/auth/login", json={"email": blocked_user.email, "password": PASSWORD})
    assert resp.status_code == 403


async def test_business_admin_cannot_suspend_own_tenant(client: AsyncClient) -> None:
    tenant = await make_tenant()
    headers = await auth_headers(client, await make_user(tenant, Role.TENANT_ADMIN))
    resp = await client.patch(f"/api/v1/tenants/{tenant.id}", json={"status": "suspended"}, headers=headers)
    assert resp.status_code == 403
