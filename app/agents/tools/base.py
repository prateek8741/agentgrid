"""Tool framework for the agent loop.

Security model:
* Tools never accept tenant ids, user ids or record ids of other customers:
  everything is resolved from the server-side `ToolContext` (tenant,
  conversation, contact), so a prompt-injected model cannot reach other
  tenants or other customers' data.
* Arguments are validated with Pydantic before execution; invalid arguments
  are returned to the model as an error it can correct, never executed.
* PII placeholders (<EMAIL_1>...) produced by redaction are restored to real
  values only here, at the tool boundary.
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.pii import Redaction
from app.models import Agent, Contact, Conversation, Tenant

log = get_logger(__name__)


@dataclass
class ToolContext:
    db: AsyncSession
    tenant: Tenant
    agent: Agent
    conversation: Conversation
    contact: Contact
    redaction: Redaction = field(default_factory=lambda: Redaction(text=""))
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    handover_reason: str | None = None
    citations: list[dict[str, Any]] = field(default_factory=list)
    actions_enqueued: bool = False

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Queue a domain event; published by the orchestrator after commit."""
        self.events.append((event_type, payload))


Handler = Callable[[ToolContext, Any], Awaitable[dict[str, Any]]]


@dataclass
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Handler

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": simplify_schema(self.args_model.model_json_schema()),
            },
        }


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, args_model: type[BaseModel]) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        REGISTRY[name] = Tool(name=name, description=description, args_model=args_model, handler=fn)
        return fn

    return register


def simplify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make Pydantic JSON schema portable across providers.

    Gemini's OpenAI-compatible endpoint rejects some JSON-schema constructs, so
    `Optional[X]` (anyOf [X, null]) becomes plain X (optionality is expressed by
    `required`), and titles / null defaults are dropped.
    """
    schema = copy.deepcopy(schema)

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            if "anyOf" in node:
                options = [o for o in node["anyOf"] if o.get("type") != "null"]
                if len(options) == 1:
                    merged = {**options[0], **{k: v for k, v in node.items() if k != "anyOf"}}
                    node = merged
            node.pop("title", None)
            if node.get("default", "x") is None:
                node.pop("default")
            return {k: clean(v) for k, v in node.items()}
        if isinstance(node, list):
            return [clean(v) for v in node]
        return node

    cleaned = clean(schema)
    cleaned.setdefault("properties", {})
    return cleaned


def _restore(value: Any, redaction: Redaction) -> Any:
    if isinstance(value, str):
        return redaction.restore(value)
    if isinstance(value, dict):
        return {k: _restore(v, redaction) for k, v in value.items()}
    if isinstance(value, list):
        return [_restore(v, redaction) for v in value]
    return value


def tool_schemas(enabled: list[str]) -> list[dict[str, Any]]:
    return [REGISTRY[name].schema() for name in enabled if name in REGISTRY]


async def run_tool(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    enabled = set(ctx.agent.enabled_tools or [])
    selected = REGISTRY.get(name)
    if selected is None or name not in enabled:
        return {"error": f"Tool '{name}' is not available"}
    try:
        args = selected.args_model.model_validate(_restore(arguments, ctx.redaction))
    except ValidationError as exc:
        details = [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()][:5]
        return {"error": "invalid arguments", "details": details}
    events_before = len(ctx.events)
    try:
        if _supports_savepoints(ctx.db):
            # A failing tool rolls back only its own writes, never the conversation's.
            async with ctx.db.begin_nested():
                return await selected.handler(ctx, args)
        return await selected.handler(ctx, args)
    except (AppError, ValueError) as exc:
        del ctx.events[events_before:]
        message = exc.message if isinstance(exc, AppError) else str(exc)
        return {"error": message}
    except Exception:
        del ctx.events[events_before:]
        log.exception("tool %s failed", name)
        return {"error": "The action failed due to an internal error. Offer to connect a human."}


def _supports_savepoints(db: AsyncSession) -> bool:
    # pysqlite's legacy transaction handling makes SAVEPOINT unreliable; Postgres is the production target.
    return db.get_bind().dialect.name == "postgresql"
