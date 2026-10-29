import pytest
from httpx import AsyncClient

from app.api.deps import resolve_api_key
from app.core.database import SessionLocal
from app.core.errors import AuthError
from app.core.rbac import Role
from app.models import ApiKeyKind
from tests.conftest import auth_headers, make_tenant, make_user


async def test_secret_key_shown_once_and_resolves_to_tenant(client: AsyncClient) -> None:
    tenant = await make_tenant()
    headers = await auth_headers(client, await make_user(tenant, Role.TENANT_ADMIN))
    created = (
        await client.post("/api/v1/api-keys", json={"name": "backend", "kind": "secret"}, headers=headers)
    ).json()
    raw = created["key"]
    assert raw.startswith("sk_") and created["public_key"] is None

    listed = (await client.get("/api/v1/api-keys", headers=headers)).json()
    assert all("key" not in k for k in listed)
    assert any(k["kind"] == "public" and k["public_key"].startswith("pk_") for k in listed)

    async with SessionLocal() as s:
        ctx = await resolve_api_key(s, raw, ApiKeyKind.SECRET)
        assert ctx.tenant_id == tenant.id
        with pytest.raises(AuthError):  # a secret key is not a public key
            await resolve_api_key(s, raw, ApiKeyKind.PUBLIC)

    revoked = await client.post(f"/api/v1/api-keys/{created['id']}/revoke", headers=headers)
    assert revoked.json()["revoked_at"] is not None
    async with SessionLocal() as s:
        with pytest.raises(AuthError):
            await resolve_api_key(s, raw, ApiKeyKind.SECRET)


async def test_garbage_keys_rejected() -> None:
    async with SessionLocal() as s:
        for bad in ("", "sk_nope", "pk_" + "a" * 200, "Bearer xyz"):
            with pytest.raises(AuthError):
                await resolve_api_key(s, bad, ApiKeyKind.SECRET)
