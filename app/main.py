"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api.console import mount_console
from app.api.health import router as health_router
from app.api.payments_mock import router as payments_mock_router
from app.api.public_pages import router as public_pages_router
from app.api.router import api_router
from app.api.webhooks import router as webhooks_router
from app.api.widget import router as widget_router
from app.core.config import settings
from app.core.database import engine
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.metrics import MetricsMiddleware, render_metrics
from app.core.middleware import RequestContextMiddleware, SplitCORSMiddleware
from app.core.observability import init_sentry, init_tracing
from app.core.redis import close_redis, get_redis, redis_mode
from app.workers.events import get_event_bus
from app.workers.maintenance import ensure_scheduled
from app.workers.registry import register_all
from app.workers.workflow import get_workflow_engine

log = get_logger(__name__)

OPENAPI_TAGS = [
    {"name": "auth", "description": "JWT login, refresh-token rotation, tenant switching"},
    {"name": "tenants", "description": "Tenants, agency hierarchy, settings and white-label branding"},
    {"name": "users", "description": "Tenant users and roles"},
    {"name": "agents", "description": "AI agents, industry templates and the test playground"},
    {"name": "knowledge", "description": "Knowledge base: document upload, chunking, embeddings, search"},
    {"name": "inbox", "description": "Human handover inbox: take over, reply live, hand back to AI"},
    {"name": "analytics", "description": "Dashboard KPIs, time series, reseller and platform views"},
    {"name": "crm", "description": "Built-in mini CRM: leads and pipeline"},
    {"name": "booking", "description": "Availability slots and appointments"},
    {"name": "commerce", "description": "Product catalog (CSV) and support tickets"},
    {"name": "operations", "description": "Conversations, workflow action log, LLM usage and quotas"},
    {"name": "api keys", "description": "Public widget keys and secret REST keys"},
    {"name": "audit", "description": "Audit trail of administrative actions"},
    {"name": "channels", "description": "REST API channel (secret key) and WhatsApp / Messenger webhooks"},
    {"name": "widget", "description": "Public web-chat widget API (public key + signed visitor session)"},
    {"name": "health", "description": "Liveness and readiness probes"},
]


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings.validate_for_production()
    get_redis()  # create the client eagerly so misconfiguration shows at boot
    register_all()
    bus = get_event_bus()
    engine_ = get_workflow_engine()
    if settings.run_workers_in_api:
        await bus.start(consume=True)
        engine_.start()
        try:
            await ensure_scheduled()
        except Exception:  # e.g. migrations not applied yet; never block startup on maintenance
            log.warning("could not schedule retention job")
    else:
        await bus.start(consume=False)
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
        await bus.close()
        await close_redis()
        await engine.dispose()
        log.info("AgentGrid stopped")


def create_app() -> FastAPI:
    configure_logging(settings.log_level, settings.log_json)
    init_sentry()
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
        SplitCORSMiddleware,
        origins=settings.cors_origins,
        public_prefixes=("/api/v1/widget", "/widget.js"),
    )
    if settings.metrics_enabled:
        app.add_middleware(MetricsMiddleware)
    app.add_middleware(RequestContextMiddleware)  # outermost: request id wraps everything
    app.include_router(health_router)
    app.include_router(api_router)
    app.include_router(payments_mock_router)
    app.include_router(widget_router)
    app.include_router(webhooks_router)
    app.include_router(public_pages_router)

    @app.get("/metrics", include_in_schema=False)
    async def metrics(request: Request) -> Response:
        token = settings.metrics_token.get_secret_value()
        if not settings.metrics_enabled:
            return Response(status_code=404)
        if token and request.headers.get("authorization") != f"Bearer {token}":
            return Response(status_code=401)
        body, content_type = render_metrics()
        return Response(body, media_type=content_type)

    init_tracing(app)
    mount_console(app)  # last: catch-all for client-side routes
    return app


app = create_app()
