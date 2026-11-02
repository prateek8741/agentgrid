import pytest
from httpx import AsyncClient

from app.core.rbac import Role, assignable_roles, role_at_least
from app.main import app
from tests.conftest import auth_headers, make_tenant, make_user

# Routes intentionally reachable without a user JWT.
PUBLIC_ROUTES = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
    # Public web-widget API: authenticated by public key + signed visitor session (tests/test_channels.py).
    ("GET", "/api/v1/widget/config"),
    ("GET", "/api/v1/widget/history"),
    ("POST", "/api/v1/widget/messages"),
}


def _api_routes() -> list[tuple[str, str]]:
    # Enumerate via the OpenAPI schema: stable across FastAPI router internals.
    out = []
    for path, operations in app.openapi()["paths"].items():
        if path.startswith("/api/v1"):
            out.extend((method.upper(), path) for method in operations)
    assert len(out) > 20, "route discovery is broken"
    return sorted(out)


def test_role_hierarchy() -> None:
    assert role_at_least(Role.SUPER_ADMIN, Role.TENANT_ADMIN)
    assert role_at_least(Role.TENANT_ADMIN, Role.AGENT)
    assert not role_at_least(Role.VIEWER, Role.AGENT)
    assert Role.SUPER_ADMIN not in assignable_roles(Role.TENANT_ADMIN)
    assert assignable_roles(Role.AGENT) == set()


@pytest.mark.parametrize(("method", "path"), [r for r in _api_routes() if r not in PUBLIC_ROUTES])
async def test_every_protected_route_requires_auth(client: AsyncClient, method: str, path: str) -> None:
    url = path.replace("{", "").replace("}", "")
    for param in ("tenant_id", "user_id", "agent_id", "key_id"):
        url = url.replace(param, "00000000-0000-0000-0000-000000000000")
    resp = await client.request(method, url, json={})
    assert resp.status_code == 401, f"{method} {path} is reachable without auth"


async def test_viewer_is_read_only(client: AsyncClient) -> None:
    tenant = await make_tenant()
    headers = await auth_headers(client, await make_user(tenant, Role.VIEWER))
    assert (await client.get("/api/v1/agents", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/tenant", headers=headers)).status_code == 200
    assert (await client.post("/api/v1/agents", json={"name": "x"}, headers=headers)).status_code == 403
    assert (await client.get("/api/v1/users", headers=headers)).status_code == 403
    assert (await client.get("/api/v1/api-keys", headers=headers)).status_code == 403


async def test_agent_can_list_users_but_not_manage(client: AsyncClient) -> None:
    tenant = await make_tenant()
    headers = await auth_headers(client, await make_user(tenant, Role.AGENT))
    assert (await client.get("/api/v1/users", headers=headers)).status_code == 200
    resp = await client.post(
        "/api/v1/users",
        json={"email": "n@example.org", "password": "Passw0rd123", "role": "viewer"},
        headers=headers,
    )
    assert resp.status_code == 403


async def test_tenant_admin_manages_users_but_cannot_grant_super_admin(client: AsyncClient) -> None:
    tenant = await make_tenant()
    admin = await make_user(tenant, Role.TENANT_ADMIN)
    headers = await auth_headers(client, admin)

    created = await client.post(
        "/api/v1/users",
        json={
            "email": "new.agent@example.org",
            "password": "Passw0rd123",
            "role": "agent",
            "full_name": "New",
        },
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["tenant_id"] == str(tenant.id)

    dup = await client.post(
        "/api/v1/users", json={"email": "new.agent@example.org", "password": "Passw0rd123"}, headers=headers
    )
    assert dup.status_code == 409

    escalate = await client.post(
        "/api/v1/users",
        json={"email": "evil@example.org", "password": "Passw0rd123", "role": "super_admin"},
        headers=headers,
    )
    assert escalate.status_code == 403

    self_demote = await client.patch(f"/api/v1/users/{admin.id}", json={"role": "viewer"}, headers=headers)
    assert self_demote.status_code == 403


async def test_only_admins_create_tenants(client: AsyncClient) -> None:
    tenant = await make_tenant()
    biz_admin = await auth_headers(client, await make_user(tenant, Role.TENANT_ADMIN))
    resp = await client.post("/api/v1/tenants", json={"name": "Sneaky Co"}, headers=biz_admin)
    assert resp.status_code == 403  # a plain business tenant admin is not an agency
