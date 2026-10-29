from httpx import AsyncClient

from app.core.rbac import Role
from tests.conftest import PASSWORD, bearer, login, make_tenant, make_user


async def test_login_and_me(client: AsyncClient) -> None:
    tenant = await make_tenant()
    user = await make_user(tenant, Role.TENANT_ADMIN)
    tokens = await login(client, user.email)
    assert tokens["token_type"] == "bearer"
    assert tokens["tenant"]["id"] == str(tenant.id)

    me = await client.get("/api/v1/auth/me", headers=bearer(tokens))
    assert me.status_code == 200
    body = me.json()
    assert body["user"]["email"] == user.email
    assert body["role"] == "tenant_admin"
    assert body["branding"]["primary_color"].startswith("#")


async def test_login_is_case_insensitive_on_email(client: AsyncClient) -> None:
    user = await make_user(await make_tenant(), Role.AGENT)
    resp = await client.post("/api/v1/auth/login", json={"email": user.email.upper(), "password": PASSWORD})
    assert resp.status_code == 200


async def test_wrong_password_and_unknown_user_look_the_same(client: AsyncClient) -> None:
    user = await make_user(await make_tenant(), Role.AGENT)
    bad_pw = await client.post("/api/v1/auth/login", json={"email": user.email, "password": "nope12345"})
    no_user = await client.post(
        "/api/v1/auth/login", json={"email": "ghost@example.org", "password": "nope12345"}
    )
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json()["error"]["message"] == no_user.json()["error"]["message"]


async def test_inactive_user_cannot_login(client: AsyncClient) -> None:
    user = await make_user(await make_tenant(), Role.AGENT, active=False)
    resp = await client.post("/api/v1/auth/login", json={"email": user.email, "password": PASSWORD})
    assert resp.status_code == 401


async def test_missing_and_invalid_tokens_rejected(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


async def test_refresh_token_cannot_be_used_as_access_token(client: AsyncClient) -> None:
    user = await make_user(await make_tenant(), Role.AGENT)
    tokens = await login(client, user.email)
    resp = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert resp.status_code == 401


async def test_refresh_rotation_and_reuse_detection(client: AsyncClient) -> None:
    user = await make_user(await make_tenant(), Role.AGENT)
    first = await login(client, user.email)

    rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200
    second = rotated.json()
    assert second["refresh_token"] != first["refresh_token"]

    # Replaying the old refresh token is treated as theft: the whole family dies.
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert replay.status_code == 401
    after = await client.post("/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]})
    assert after.status_code == 401


async def test_logout_revokes_refresh_token(client: AsyncClient) -> None:
    user = await make_user(await make_tenant(), Role.AGENT)
    tokens = await login(client, user.email)
    assert (
        await client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 200
    resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert resp.status_code == 401


async def test_deactivated_user_token_stops_working_immediately(client: AsyncClient) -> None:
    tenant = await make_tenant()
    admin = await make_user(tenant, Role.TENANT_ADMIN)
    agent = await make_user(tenant, Role.AGENT)
    agent_headers = bearer(await login(client, agent.email))
    admin_headers = bearer(await login(client, admin.email))

    resp = await client.patch(f"/api/v1/users/{agent.id}", json={"is_active": False}, headers=admin_headers)
    assert resp.status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=agent_headers)).status_code == 401


async def test_change_password(client: AsyncClient) -> None:
    user = await make_user(await make_tenant(), Role.VIEWER)
    tokens = await login(client, user.email)
    resp = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": PASSWORD, "new_password": "BrandNew123"},
        headers=bearer(tokens),
    )
    assert resp.status_code == 200
    assert (
        await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    ).status_code == 401
    await login(client, user.email, "BrandNew123")


async def test_weak_password_rejected(client: AsyncClient) -> None:
    tenant = await make_tenant()
    admin = await make_user(tenant, Role.TENANT_ADMIN)
    resp = await client.post(
        "/api/v1/users",
        json={"email": "weak@example.org", "password": "short", "role": "agent"},
        headers=bearer(await login(client, admin.email)),
    )
    assert resp.status_code == 422


async def test_login_rate_limited(client: AsyncClient) -> None:
    payload = {"email": "brute@example.org", "password": "wrong-password1"}
    codes = [(await client.post("/api/v1/auth/login", json=payload)).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10
    assert codes[-1] == 429
    resp = await client.post("/api/v1/auth/login", json=payload)
    assert "retry-after" in resp.headers
