"""Lightweight tool-calling agent loop (no framework).

LLM -> tool calls -> validated execution -> results back to the LLM -> ...
until the model answers in text or `max_iterations` is reached, in which case
one final tool-free call forces an answer.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.llm.base import ChatMessage, LLMError, LLMResponse
from app.agents.llm.router import LLMRouter
from app.agents.tools.base import ToolContext, run_tool
from app.core.logging import get_logger

log = get_logger(__name__)

UsageCallback = Callable[[LLMResponse], Awaitable[None]]
MAX_TOOL_RESULT_CHARS = 4000


@dataclass
class LoopResult:
    text: str | None
    responses: list[LLMResponse] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    iterations: int = 0
    error: str | None = None

    @property
    def tokens_in(self) -> int:
        return sum(r.prompt_tokens for r in self.responses)

    @property
    def tokens_out(self) -> int:
        return sum(r.completion_tokens for r in self.responses)

    @property
    def last(self) -> LLMResponse | None:
        return self.responses[-1] if self.responses else None


async def run_agent_loop(
    router: LLMRouter,
    *,
    system_prompt: str,
    history: list[dict[str, str]],
    user_text: str,
    tools: list[dict[str, Any]],
    ctx: ToolContext,
    on_usage: UsageCallback,
    model: str | None = None,
    temperature: float = 0.3,
    max_iterations: int = 5,
) -> LoopResult:
    protect = ctx.redaction.protect
    messages: list[ChatMessage] = [{"role": "system", "content": system_prompt}]
    messages += [{"role": h["role"], "content": protect(h["content"])} for h in history]
    messages.append({"role": "user", "content": protect(user_text)})
    result = LoopResult(text=None)

    for _ in range(max_iterations):
        result.iterations += 1
        try:
            response = await router.chat(
                messages,
                model=model,
                purpose="chat",
                tools=tools or None,
                temperature=temperature,
                max_tokens=700,
            )
        except LLMError as exc:
            result.error = exc.code
            log.warning("agent loop LLM failure: %s", exc)
            return result
        result.responses.append(response)
        await on_usage(response)

        if not response.tool_calls:
            result.text = response.content
            return result

        messages.append(
            {
                "role": "assistant",
                "content": response.content or "",
                "tool_calls": [call.to_message() for call in response.tool_calls],
            }
        )
        for call in response.tool_calls:
            if call.parse_error:
                output: dict[str, Any] = {"error": call.parse_error}
            else:
                output = await run_tool(ctx, call.name, call.arguments)
            result.tool_calls.append(
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "ok": "error" not in output,
                    "error": output.get("error"),
                }
            )
            content = json.dumps(output, default=str, ensure_ascii=False)[:MAX_TOOL_RESULT_CHARS]
            messages.append({"role": "tool", "tool_call_id": call.id, "content": protect(content)})

    # Iteration budget exhausted: force a final answer without tools.
    messages.append({"role": "system", "content": "Respond to the customer now in plain text without tools."})
    try:
        response = await router.chat(
            messages, model=model, purpose="chat", temperature=temperature, max_tokens=500
        )
    except LLMError as exc:
        result.error = exc.code
        return result
    result.responses.append(response)
    await on_usage(response)
    result.text = response.content
    return result
