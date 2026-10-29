import hashlib
import hmac

import pytest

from app.core.config import Settings
from app.core.pii import mask_pii, redact_for_llm
from app.core.security import TokenError, create_token, decode_token, sign_payload, verify_hub_signature


def test_mask_pii_in_logs() -> None:
    text = "Contact rahul.sharma@gmail.com or +91 98765 43210, card 4111 1111 1111 1111"
    masked = mask_pii(text)
    assert "rahul.sharma" not in masked and "r***@gmail.com" in masked
    assert "98765" not in masked and "***3210" in masked
    assert "4111" not in masked and "[CARD]" in masked


def test_dates_and_times_are_not_treated_as_phones() -> None:
    for text in ("Book me on 2026-11-02 10:30", "Visit on 02/11/2026 please", "Budget is 50 lakh"):
        assert redact_for_llm(text).text == text
        assert mask_pii(text) == text


def test_llm_redaction_is_reversible() -> None:
    red = redact_for_llm("I'm priya@example.org, call 9876543210. Again: priya@example.org")
    assert "priya@" not in red.text and "9876543210" not in red.text
    assert red.text.count("<EMAIL_1>") == 2 and "<PHONE_1>" in red.text
    assert red.restore("save <EMAIL_1> / <PHONE_1>") == "save priya@example.org / 9876543210"


def test_jwt_round_trip_and_type_enforcement() -> None:
    import uuid

    uid, tid = uuid.uuid4(), uuid.uuid4()
    token, _ = create_token(user_id=uid, tenant_id=tid, home_tenant_id=tid, role="agent", token_type="access")
    payload = decode_token(token, "access")
    assert payload.sub == uid and payload.tid == tid and payload.role == "agent"
    with pytest.raises(TokenError):
        decode_token(token, "refresh")
    with pytest.raises(TokenError):
        decode_token(token[:-4] + "AAAA", "access")


def test_hub_signature_verification() -> None:
    secret, body = "app-secret", b'{"object":"whatsapp_business_account"}'
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_hub_signature(secret, body, good)
    assert sign_payload(secret, body) == good
    assert not verify_hub_signature(secret, body + b" ", good)
    assert not verify_hub_signature(secret, body, None)
    assert not verify_hub_signature("", body, good)
    assert not verify_hub_signature(secret, body, good.replace("sha256=", "sha1="))


def test_database_url_normalisation() -> None:
    neon = Settings(
        database_url="postgresql://u:p@ep-cool-1.aws.neon.tech/db?sslmode=require&channel_binding=require"
    )
    url, args = neon.async_database_url()
    assert url == "postgresql+asyncpg://u:p@ep-cool-1.aws.neon.tech/db"
    assert args == {"ssl": "require"}

    supa = Settings(database_url="postgres://u:p@aws-0-ap-south-1.pooler.supabase.com:6543/postgres")
    url, args = supa.async_database_url()
    assert url.startswith("postgresql+asyncpg://")
    assert args["statement_cache_size"] == 0

    lite = Settings(database_url="sqlite:///./x.db")
    assert lite.async_database_url()[0].startswith("sqlite+aiosqlite://")


def test_production_config_guard() -> None:
    with pytest.raises(RuntimeError):
        Settings(app_env="production").validate_for_production()
    Settings(
        app_env="production",
        jwt_secret="x" * 40,
        superadmin_password="Another-Strong-1",
        cors_origins="https://a.io",
    ).validate_for_production()


def test_cors_origins_csv_parsing() -> None:
    assert Settings(cors_origins="https://a.io, https://b.io").cors_origins == [
        "https://a.io",
        "https://b.io",
    ]
