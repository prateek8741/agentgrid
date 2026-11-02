"""Prometheus metrics (/metrics).

Request latency, LLM latency / tokens / errors per tenant and provider,
agent replies by decision, handovers, domain events. Agent-level metrics are
fed from domain events so business code stays free of metrics plumbing.
"""

from __future__ import annotations

import time

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

HTTP_REQUESTS = Counter("agentgrid_http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "agentgrid_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
LLM_REQUESTS = Counter("agentgrid_llm_requests_total", "LLM calls", ["provider", "purpose", "outcome"])
LLM_LATENCY = Histogram(
    "agentgrid_llm_latency_seconds",
    "LLM call latency",
    ["provider", "purpose"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 30),
)
LLM_TOKENS = Counter("agentgrid_llm_tokens_total", "LLM tokens", ["tenant", "provider", "kind"])
AGENT_REPLIES = Counter("agentgrid_agent_replies_total", "Agent replies", ["tenant", "channel", "decision"])
AGENT_RESPONSE = Histogram(
    "agentgrid_agent_response_seconds",
    "Inbound message to reply",
    ["channel"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8),
)
HANDOVERS = Counter("agentgrid_handovers_total", "Escalations to humans", ["tenant", "reason"])
EVENTS = Counter("agentgrid_events_total", "Domain events published", ["type"])


def render_metrics() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


class MetricsMiddleware:
    """Records request count/latency labelled by route *template* (bounded cardinality)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return
        start = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            method = scope.get("method", "")
            HTTP_REQUESTS.labels(method, template, str(status["code"])).inc()
            HTTP_LATENCY.labels(method, template).observe(time.perf_counter() - start)


async def record_event_metrics(event: object) -> None:
    """Domain-event subscriber (registered at startup)."""
    etype = getattr(event, "type", "")
    payload = getattr(event, "payload", {}) or {}
    tenant = str(getattr(event, "tenant_id", ""))
    EVENTS.labels(etype).inc()
    if etype == "message.sent":
        channel = str(payload.get("channel", ""))
        AGENT_REPLIES.labels(tenant, channel, str(payload.get("decision", ""))).inc()
        if payload.get("latency_ms") is not None:
            AGENT_RESPONSE.labels(channel).observe(float(payload["latency_ms"]) / 1000)
    elif etype == "handover.requested":
        HANDOVERS.labels(tenant, str(payload.get("reason", ""))).inc()
