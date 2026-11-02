"""Optional Sentry and OpenTelemetry hooks (off by default, zero cost when unused).

Install extras with `pip install -r requirements-observability.txt`, then set
SENTRY_DSN and/or OTEL_ENABLED=true (+ OTEL_EXPORTER_OTLP_ENDPOINT).
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.core.pii import mask_pii

log = get_logger(__name__)


def _scrub(event: dict[str, Any], _hint: Any) -> dict[str, Any]:
    """Sentry before_send: mask PII in messages and request bodies."""
    for exc in (event.get("exception") or {}).get("values", []) or []:
        if isinstance(exc.get("value"), str):
            exc["value"] = mask_pii(exc["value"])
    if isinstance((event.get("logentry") or {}).get("message"), str):
        event["logentry"]["message"] = mask_pii(event["logentry"]["message"])
    (event.get("request") or {}).pop("data", None)
    return event


def init_sentry() -> None:
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        log.warning("SENTRY_DSN set but sentry-sdk is not installed (requirements-observability.txt)")
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        traces_sample_rate=0.1,
        send_default_pii=False,
        before_send=_scrub,
    )
    log.info("Sentry enabled")


def init_tracing(app: Any) -> None:
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning(
            "OTEL_ENABLED but OpenTelemetry packages are not installed (requirements-observability.txt)"
        )
        return
    provider = TracerProvider(resource=Resource.create({"service.name": "agentgrid-api"}))
    exporter = OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint or None)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,ready,metrics")
    log.info("OpenTelemetry tracing enabled")
