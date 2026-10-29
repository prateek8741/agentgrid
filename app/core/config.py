"""Application settings loaded from environment variables / `.env`.

All configuration lives here so the rest of the code never reads `os.environ`
directly. Secrets are never hard-coded; defaults are safe for local development
and `validate_for_production()` refuses to boot with insecure values.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parents[2]

_INSECURE_JWT_DEFAULT = "change-me-to-a-long-random-string-at-least-32-chars"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- App ----
    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "AgentGrid"
    public_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    log_json: bool = True
    web_concurrency: int = Field(default=1, ge=1, le=64)
    host: str = "0.0.0.0"  # noqa: S104 - bind address for containers
    port: int = 8000

    # ---- Security ----
    jwt_secret: SecretStr = SecretStr(_INSECURE_JWT_DEFAULT)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "agentgrid"
    jwt_access_ttl_minutes: int = Field(default=30, ge=1)
    jwt_refresh_ttl_days: int = Field(default=14, ge=1)
    bcrypt_rounds: int = Field(default=12, ge=4, le=16)
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:8000", "http://localhost:5173"]
    superadmin_email: str = "admin@agentgrid.dev"
    superadmin_password: SecretStr = SecretStr("ChangeMe123!")

    # ---- Database ----
    database_url: str = "postgresql+asyncpg://agentgrid:agentgrid@localhost:5432/agentgrid"
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_timeout: int = 30
    db_pool_recycle: int = 300
    db_echo: bool = False
    db_disable_prepared_statements: bool = False

    # ---- Redis / events ----
    redis_url: str = ""
    kafka_bootstrap_servers: str = ""
    kafka_topic_prefix: str = "agentgrid"

    # ---- LLM ----
    groq_api_key: SecretStr = SecretStr("")
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-2.0-flash"
    llm_timeout_seconds: float = 30.0

    # ---- Limits / quotas ----
    rate_limit_per_minute: int = 60
    login_rate_limit_per_minute: int = 10
    default_monthly_token_quota: int = 2_000_000
    default_retention_days: int = 365

    # ---- Uploads / RAG ----
    upload_max_mb: int = 10
    max_chunks_per_document: int = 400
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_cache_dir: str = ".cache/fastembed"

    # ---- Channels ----
    whatsapp_verify_token: SecretStr = SecretStr("")
    whatsapp_app_secret: SecretStr = SecretStr("")
    whatsapp_access_token: SecretStr = SecretStr("")
    whatsapp_phone_number_id: str = ""
    messenger_verify_token: SecretStr = SecretStr("")
    messenger_app_secret: SecretStr = SecretStr("")
    messenger_page_access_token: SecretStr = SecretStr("")

    # ---- Notifications ----
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = "AgentGrid <no-reply@agentgrid.dev>"
    smtp_starttls: bool = True

    # ---- Integrations ----
    hubspot_private_app_token: SecretStr = SecretStr("")
    razorpay_key_id: str = ""
    razorpay_key_secret: SecretStr = SecretStr("")

    # ---- Observability ----
    sentry_dsn: str = ""
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str = ""
    metrics_enabled: bool = True

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    # ---- Derived helpers ----
    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def upload_max_bytes(self) -> int:
        return self.upload_max_mb * 1024 * 1024

    @property
    def data_dir(self) -> Path:
        return BASE_DIR / "data"

    def async_database_url(self) -> tuple[str, dict[str, object]]:
        """Normalise DATABASE_URL for SQLAlchemy+asyncpg.

        Neon/Supabase hand out `postgres://...?sslmode=require` URLs. asyncpg does not
        understand libpq query params, so we translate them into `connect_args`.
        Returns `(url, connect_args)`.
        """
        url = self.database_url.strip()
        if url.startswith("sqlite"):
            if "+aiosqlite" not in url:
                url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)
            return url, {}

        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                url = "postgresql+asyncpg://" + url[len(prefix) :]
                break

        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query))
        connect_args: dict[str, object] = {}
        sslmode = query.pop("sslmode", None)
        query.pop("channel_binding", None)  # libpq-only option (Neon)
        if sslmode in {"require", "verify-ca", "verify-full", "prefer"}:
            connect_args["ssl"] = "require" if sslmode in {"require", "prefer"} else sslmode
        if "ssl" in query:
            connect_args["ssl"] = query.pop("ssl")

        host = parts.hostname or ""
        uses_pgbouncer = parts.port == 6543 or "pooler" in host
        if self.db_disable_prepared_statements or uses_pgbouncer:
            # PgBouncer in transaction mode cannot track prepared statements.
            connect_args["statement_cache_size"] = 0
            connect_args["prepared_statement_cache_size"] = 0

        clean = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        return clean, connect_args

    def validate_for_production(self) -> None:
        """Fail fast on insecure configuration when APP_ENV=production."""
        if not self.is_production:
            return
        problems: list[str] = []
        secret = self.jwt_secret.get_secret_value()
        if secret == _INSECURE_JWT_DEFAULT or len(secret) < 32:
            problems.append("JWT_SECRET must be set to a random value of at least 32 characters")
        if self.superadmin_password.get_secret_value() == "ChangeMe123!":
            problems.append("SUPERADMIN_PASSWORD must be changed from the default")
        if "*" in self.cors_origins:
            problems.append("CORS_ORIGINS must not contain '*' in production")
        if problems:
            raise RuntimeError("Insecure production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
