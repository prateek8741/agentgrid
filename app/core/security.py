"""Password hashing, JWT tokens, API keys and webhook signatures."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import anyio
import bcrypt
import jwt

from app.core.config import settings

TokenType = Literal["access", "refresh"]

PUBLIC_KEY_PREFIX = "pk_"
SECRET_KEY_PREFIX = "sk_"


class TokenError(Exception):
    """Raised for any invalid, expired or wrong-type token."""


# ---------------------------------------------------------------- passwords
def _hash_password_sync(password: str) -> str:
    salt = bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("ascii")


def _verify_password_sync(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except ValueError:
        return False


async def hash_password(password: str) -> str:
    # bcrypt is deliberately slow; keep it off the event loop.
    return await anyio.to_thread.run_sync(_hash_password_sync, password)


async def verify_password(password: str, password_hash: str) -> bool:
    return await anyio.to_thread.run_sync(_verify_password_sync, password, password_hash)


# A real hash used to equalise timing when the user does not exist.
_DUMMY_HASH = bcrypt.hashpw(b"agentgrid-timing-equaliser", bcrypt.gensalt(rounds=4)).decode("ascii")


async def verify_password_dummy() -> None:
    await anyio.to_thread.run_sync(_verify_password_sync, "x", _DUMMY_HASH)


# ---------------------------------------------------------------- JWT
@dataclass(frozen=True)
class TokenPayload:
    sub: uuid.UUID  # user id
    tid: uuid.UUID  # active tenant id (may be a client of the user's agency)
    htid: uuid.UUID  # user's home tenant id
    role: str
    type: TokenType
    jti: str
    exp: datetime
    fid: uuid.UUID | None = None  # refresh-token family id


def create_token(
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID,
    home_tenant_id: uuid.UUID,
    role: str,
    token_type: TokenType,
    family_id: uuid.UUID | None = None,
) -> tuple[str, TokenPayload]:
    now = datetime.now(UTC)
    if token_type == "access":
        expires = now + timedelta(minutes=settings.jwt_access_ttl_minutes)
    else:
        expires = now + timedelta(days=settings.jwt_refresh_ttl_days)
    jti = secrets.token_hex(16)
    claims: dict[str, Any] = {
        "iss": settings.jwt_issuer,
        "sub": str(user_id),
        "tid": str(tenant_id),
        "htid": str(home_tenant_id),
        "role": role,
        "type": token_type,
        "jti": jti,
        "iat": now,
        "nbf": now,
        "exp": expires,
    }
    if family_id is not None:
        claims["fid"] = str(family_id)
    token = jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)
    payload = TokenPayload(
        sub=user_id,
        tid=tenant_id,
        htid=home_tenant_id,
        role=role,
        type=token_type,
        jti=jti,
        exp=expires,
        fid=family_id,
    )
    return token, payload


def decode_token(token: str, expected_type: TokenType) -> TokenPayload:
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "tid", "type", "jti"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise TokenError("Token expired") from exc
    except jwt.PyJWTError as exc:
        raise TokenError("Invalid token") from exc
    if claims.get("type") != expected_type:
        raise TokenError("Wrong token type")
    try:
        return TokenPayload(
            sub=uuid.UUID(claims["sub"]),
            tid=uuid.UUID(claims["tid"]),
            htid=uuid.UUID(claims.get("htid", claims["tid"])),
            role=str(claims.get("role", "")),
            type=claims["type"],
            jti=str(claims["jti"]),
            exp=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
            fid=uuid.UUID(claims["fid"]) if claims.get("fid") else None,
        )
    except (ValueError, KeyError) as exc:
        raise TokenError("Malformed token claims") from exc


# ---------------------------------------------------------------- API keys
def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_api_key(kind: Literal["public", "secret"]) -> tuple[str, str, str]:
    """Return `(raw_key, prefix, sha256_hash)`."""
    prefix = PUBLIC_KEY_PREFIX if kind == "public" else SECRET_KEY_PREFIX
    raw = prefix + secrets.token_urlsafe(32 if kind == "secret" else 18)
    return raw, raw[:12], hash_api_key(raw)


# ---------------------------------------------------------------- webhooks
def verify_hub_signature(app_secret: str, body: bytes, signature_header: str | None) -> bool:
    """Verify Meta's `X-Hub-Signature-256: sha256=<hex>` header (constant time)."""
    if not app_secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


def sign_payload(secret: str, body: bytes) -> str:
    """Signature for outgoing webhooks (receivers verify the same way)."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
