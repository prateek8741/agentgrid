"""Provider-neutral LLM types (OpenAI chat-completions wire format)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

ChatMessage = dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    raw_arguments: str = ""
    parse_error: str | None = None

    def to_message(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.name, "arguments": self.raw_arguments},
        }


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    finish_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class LLMError(Exception):
    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        status: int | None = None,
        retryable: bool = True,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after

    @property
    def code(self) -> str:
        return f"http_{self.status}" if self.status else "transport"


class LLMUnavailableError(LLMError):
    """No provider configured, or every provider failed."""


class LLMProvider(Protocol):
    name: str
    default_model: str
    nlu_model: str

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
    ) -> LLMResponse: ...
