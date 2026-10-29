from httpx import AsyncClient


async def test_health(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    assert len(resp.headers["x-request-id"]) >= 8
    assert resp.headers["x-content-type-options"] == "nosniff"


async def test_ready_checks_dependencies(client: AsyncClient) -> None:
    resp = await client.get("/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["checks"] == {"database": "ok", "redis": "ok"}
    assert body["event_bus"] == "in-process"


async def test_request_id_is_propagated(client: AsyncClient) -> None:
    resp = await client.get("/health", headers={"X-Request-ID": "abc12345-req"})
    assert resp.headers["x-request-id"] == "abc12345-req"


async def test_openapi_docs_available(client: AsyncClient) -> None:
    assert (await client.get("/docs")).status_code == 200
    spec = (await client.get("/openapi.json")).json()
    assert "/api/v1/auth/login" in spec["paths"]
