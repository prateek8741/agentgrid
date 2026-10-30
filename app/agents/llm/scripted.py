"""Deterministic LLM provider for tests and the simulation harness.

Responses are produced by a user-supplied function of the request, so tests
can assert exactly what the "model" received (e.g. that PII was redacted).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.llm.base import ChatMessage, LLMError, LLMResponse, ToolCall


@dataclass
class ScriptedCall:
    messages: list[ChatMessage]
    tools: list[dict[str, Any]] | None
    json_mode: bool
    model: str | None


Responder = Callable[[ScriptedCall], LLMResponse | LLMError]


def reply(text: str) -> LLMResponse:
    return LLMResponse(
        content=text, prompt_tokens=50, completion_tokens=20, provider="scripted", model="scripted"
    )


def tool_call(name: str, arguments: dict[str, Any], call_id: str = "call_1") -> LLMResponse:
    return LLMResponse(
        content=None,
        tool_calls=[
            ToolCall(id=call_id, name=name, arguments=arguments, raw_arguments=json.dumps(arguments))
        ],
        prompt_tokens=60,
        completion_tokens=15,
        provider="scripted",
        model="scripted",
    )


def nlu_json(intent: str, confidence: float = 0.9, sentiment: float = 0.0, **entities: Any) -> LLMResponse:
    payload = {"intent": intent, "confidence": confidence, "sentiment": sentiment, "entities": entities}
    return LLMResponse(
        content=json.dumps(payload),
        prompt_tokens=40,
        completion_tokens=20,
        provider="scripted",
        model="scripted-nlu",
    )


@dataclass
class ScriptedProvider:
    responder: Responder
    name: str = "scripted"
    default_model: str = "scripted"
    nlu_model: str = "scripted-nlu"
    calls: list[ScriptedCall] = field(default_factory=list)

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        temperature: float = 0.3,
        max_tokens: int = 800,
        json_mode: bool = False,
        model: str | None = None,
    ) -> LLMResponse:
        call = ScriptedCall(messages=messages, tools=tools, json_mode=json_mode, model=model)
        self.calls.append(call)
        result = self.responder(call)
        if isinstance(result, LLMError):
            raise result
        return result
