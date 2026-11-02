"""Standalone background worker: `python -m app.workers`.

Runs the workflow engine and the event-bus consumers (analytics, webhooks,
live inbox) without serving HTTP, so API replicas can scale independently.
Requires Kafka (KAFKA_BOOTSTRAP_SERVERS) so events cross process boundaries;
deploy API replicas with RUN_WORKERS_IN_API=false.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.workers.events import get_event_bus
from app.workers.maintenance import ensure_scheduled
from app.workers.registry import register_all
from app.workers.workflow import get_workflow_engine

log = get_logger("agentgrid.worker")


async def main() -> None:
    configure_logging(settings.log_level, settings.log_json)
    if not settings.kafka_bootstrap_servers:
        log.warning("KAFKA_BOOTSTRAP_SERVERS is not set: only workflow actions will run in this process")
    register_all()
    bus = get_event_bus()
    await bus.start(consume=True)
    engine = get_workflow_engine()
    engine.start()
    await ensure_scheduled()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # Windows: Ctrl+C raises KeyboardInterrupt instead
            loop.add_signal_handler(sig, stop.set)
    log.info("worker started")
    try:
        await stop.wait()
    finally:
        await engine.stop()
        await bus.close()
        log.info("worker stopped")


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
