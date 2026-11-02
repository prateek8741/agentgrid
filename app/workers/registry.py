"""Single place that wires domain-event subscribers and action handlers."""

from __future__ import annotations

from app.core.metrics import record_event_metrics
from app.workers import maintenance  # noqa: F401 - registers the retention action handler
from app.workers.events import get_event_bus

_registered = False


def register_all() -> None:
    """Idempotent: webhook fan-out, live inbox, analytics consumer, metrics."""
    global _registered
    if _registered:
        return
    from app.services.inbox_service import register_inbox_subscriber
    from app.workers.actions import register_subscribers
    from app.workers.analytics import register_analytics_consumer

    register_subscribers()
    register_inbox_subscriber()
    register_analytics_consumer()
    get_event_bus().subscribe("*", record_event_metrics)
    _registered = True
