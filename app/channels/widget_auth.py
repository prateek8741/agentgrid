"""Signed widget sessions.

The public widget key only identifies the tenant. Each browser gets a random
session id plus an HMAC token bound to (tenant, session), so one visitor can
never read or post into another visitor's conversation by guessing ids.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid

from app.core.config import settings

_SESSION_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def _sign(tenant_id: uuid.UUID, session_id: str) -> str:
    key = settings.jwt_secret.get_secret_value().encode()
    return hmac.new(key, f"widget:{tenant_id}:{session_id}".encode(), hashlib.sha256).hexdigest()[:40]


def new_session(tenant_id: uuid.UUID) -> tuple[str, str]:
    session_id = "w_" + secrets.token_urlsafe(18)
    return session_id, _sign(tenant_id, session_id)


def verify_session(tenant_id: uuid.UUID, session_id: str | None, token: str | None) -> bool:
    if not session_id or not token or not _SESSION_RE.match(session_id):
        return False
    return hmac.compare_digest(_sign(tenant_id, session_id), token)
