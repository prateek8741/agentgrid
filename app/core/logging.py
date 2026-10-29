"""Structured JSON logging with request IDs, tenant context and PII masking."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from app.core.pii import mask_pii

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
tenant_id_var: ContextVar[str | None] = ContextVar("tenant_id", default=None)

# Attributes present on every LogRecord; anything else passed via `extra=` is
# emitted as a structured field.
_RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}


def _mask(value: Any) -> Any:
    if isinstance(value, str):
        return mask_pii(value)
    if isinstance(value, dict):
        return {k: _mask(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_mask(v) for v in value]
    return value


class ContextFilter(logging.Filter):
    """Attach request/tenant ids and mask PII in the rendered message."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.tenant_id = tenant_id_var.get()
        try:
            message = record.getMessage()
        except Exception:  # malformed %-format args must never crash logging
            message = str(record.msg)
        record.msg = mask_pii(message)
        record.args = None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if getattr(record, "request_id", None):
            payload["request_id"] = record.request_id
        if getattr(record, "tenant_id", None):
            payload["tenant_id"] = record.tenant_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload and key not in {"request_id", "tenant_id"}:
                payload[key] = _mask(value)
        if record.exc_info:
            payload["exc"] = mask_pii(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        rid = getattr(record, "request_id", None)
        return f"{base} [rid={rid}]" if rid else base


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    # Windows consoles may default to cp1252; never crash on non-ASCII log text.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(errors="backslashreplace")
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(ContextFilter())
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())

    # Route uvicorn logs through the same handler; silence noisy libraries.
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    logging.getLogger("uvicorn.access").disabled = True  # replaced by our access log middleware
    for noisy in ("httpx", "httpcore", "aiokafka", "asyncio", "sqlalchemy.engine.Engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
