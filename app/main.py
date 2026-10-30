"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.payments_mock import router as payments_mock_router
from app.api.router import api_router
from app.core.config import settings
from app.core.database import engine
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import RequestContextMiddleware
from app.core.redis import close_redis, get_redis, redis_mode
from app.workers.actions import register_subscribers
from app.workers.events import get_event_bus
from app.workers.workflow import get_workflow_engine

log = get_logger(__name__)

OPENAPI_TAGS = [
    {"name": "auth", "description": "JWT login, refresh-token rotation, tenant switching"},
    {"name": "tenants", "description": "Tenants, agency hierarchy, settings and white-label branding"},
    {"name": "users", "description": "Tenant users and roles"},
    {"name": "agents", "description": "AI agents, industry templates and the test playground"},
    {"name": "crm", "description": "Built-in mini CRM: leads and pipeline"},
    {"name": "booking", "description": "Availability slots and appointments"},
    {"name": "commerce", "description": "Product catalog (CSV) and support tickets"},
    {"name": "operations", "description": "Conversations, workflow action log, LLM usage and quotas"},
    {"name": "api keys", "description": "Public widget keys and secret REST keys"},
    {"name": "audit", "description": "Audit trail of administrative actions"},
    {"name": "health", "description": "Liveness and readiness probes"},
]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings.validate_for_production()
    get_redis()  # create the client eagerly so misconfiguration shows at boot
    register_subscribers()
    engine_ = get_workflow_engine()
    engine_.start()
    log.info(
        "AgentGrid starting",
        extra={
            "env": settings.app_env,
            "db": "sqlite" if settings.is_sqlite else "postgres",
            "redis": redis_mode(),
            "event_bus": "kafka" if settings.kafka_bootstrap_servers else "in-process",
        },
    )
    try:
        yield
    finally:
        await engine_.stop()
        await get_event_bus().close()
        await close_redis()
        await engine.dispose()
        log.info("AgentGrid stopped")


def create_app() -> FastAPI:
    configure_logging(settings.log_level, settings.log_json)
    app = FastAPI(
        title=f"{settings.app_name} API",
        version="0.1.0",
        description=(
            "AgentGrid - AI-powered customer engagement & business automation platform.\n\n"
            "Authenticate with `POST /api/v1/auth/login`, then click **Authorize** and paste the "
            "`access_token`."
        ),
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )
    register_exception_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Request-ID"],
        expose_headers=["X-Request-ID", "Retry-After"],
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)  # outermost: request id wraps everything
    app.include_router(health_router)
    app.include_router(api_router)
    app.include_router(payments_mock_router)
    return app


app = create_app()
