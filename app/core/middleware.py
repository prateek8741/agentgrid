"""Pure-ASGI middleware (works for HTTP and WebSocket, no BaseHTTPMiddleware overhead)."""

from __future__ import annotations

import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import get_logger, request_id_var, tenant_id_var

access_log = get_logger("agentgrid.access")
_VALID_RID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

SECURITY_HEADERS: list[tuple[bytes, bytes]] = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"x-frame-options", b"SAMEORIGIN"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
]


class RequestContextMiddleware:
    """Assigns a request id, emits one structured access-log line per request
    and adds security headers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_RID.match(incoming) else uuid.uuid4().hex
        rid_token = request_id_var.set(request_id)
        tid_token = tenant_id_var.set(None)
        scope.setdefault("state", {})["request_id"] = request_id

        start = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                existing = {k.lower() for k, _ in headers}
                headers.extend(h for h in SECURITY_HEADERS if h[0] not in existing)
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if scope["type"] == "http":
                path = scope.get("path", "")
                if path not in ("/health", "/metrics"):
                    access_log.info(
                        "%s %s %s",
                        scope.get("method"),
                        path,
                        status_holder["status"],
                        extra={
                            "method": scope.get("method"),
                            "path": path,
                            "status": status_holder["status"],
                            "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                            "client_ip": (scope.get("client") or ("",))[0],
                        },
                    )
            request_id_var.reset(rid_token)
            tenant_id_var.reset(tid_token)
