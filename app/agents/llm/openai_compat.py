"""One HTTP client for every OpenAI-compatible chat API.

Groq    -> https://api.groq.com/openai/v1
Gemini  -> https://generativelanguage.googleapis.com/v1beta/openai
No vendor SDKs: plain httpx keeps the install small and identical on every OS.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from app.agents.llm.base import ChatMessage, LLMError, LLMResponse, ToolCall
from app.core.logging import get_logger

log = get_logger(__name__)

# Provider-side tool-call generation failures: another provider may well succeed.
_RETRYABLE_400_MARKERS = ("tool_use_failed", "failed_generation", "tool call validation")


def _retry_after(resp: httpx.Response) -> float | None:
    value = resp.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        default_model: str,
        nlu_model: str | None = None,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.default_model = default_model
        self.nlu_model = nlu_model or default_model
        self._timeout = timeout
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=10.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=5),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

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
        model = model or self.default_model
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        started = time.perf_counter()
        try:
            resp = await self._http().post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.TimeoutException as exc:
            raise LLMError(f"{self.name} timed out", provider=self.name) from exc
        except httpx.TransportError as exc:
            raise LLMError(f"{self.name} transport error: {type(exc).__name__}", provider=self.name) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if resp.status_code != 200:
            text = resp.text[:500]
            status = resp.status_code
            retryable = status == 429 or status >= 500 or status in (401, 403, 404)
            if status == 400 and any(marker in text for marker in _RETRYABLE_400_MARKERS):
                retryable = True
            raise LLMError(
                f"{self.name} HTTP {status}: {text}",
                provider=self.name,
                status=status,
                retryable=retryable,
                retry_after=_retry_after(resp),
            )

        try:
            data = resp.json()
            choice = data["choices"][0]
            message = choice.get("message") or {}
        except (ValueError, KeyError, IndexError) as exc:
            raise LLMError(f"{self.name} returned an unexpected payload", provider=self.name) from exc

        tool_calls: list[ToolCall] = []
        for i, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else dict(raw)
                if not isinstance(args, dict):
                    raise ValueError("arguments must be an object")
                parse_error = None
            except (ValueError, TypeError) as exc:
                args, parse_error = {}, f"invalid JSON arguments: {exc}"
            tool_calls.append(
                ToolCall(
                    id=call.get("id") or f"call_{i}",
                    name=fn.get("name", ""),
                    arguments=args,
                    raw_arguments=raw if isinstance(raw, str) else json.dumps(raw),
                    parse_error=parse_error,
                )
            )

        usage = data.get("usage") or {}
        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            provider=self.name,
            model=data.get("model") or model,
            latency_ms=latency_ms,
            finish_reason=choice.get("finish_reason"),
        )
