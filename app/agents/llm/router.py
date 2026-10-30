"""Ordered provider failover with per-provider cooldowns.

Groq is tried first (fast, generous free tier), Gemini second. A provider
that answers 429 / 5xx / auth errors / timeouts is put on a cooldown so the
next requests skip it immediately instead of paying the latency again.
"""

from __future__ import annotations

import time
from typing import Any

from app.agents.llm.base import ChatMessage, LLMError, LLMProvider, LLMResponse, LLMUnavailableError
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"


def _cooldown_for(err: LLMError) -> float:
    if err.status == 429:
        return min(max(err.retry_after or 20.0, 5.0), 300.0)
    if err.status in (401, 403):
        return 300.0  # bad key: don't hammer it
    if err.status == 404:
        return 120.0  # unknown model
    if err.status is None or err.status >= 500:
        return 15.0
    return 0.0


class LLMRouter:
    def __init__(self, providers: list[LLMProvider]) -> None:
        self.providers = providers
        self._cooldown_until: dict[str, float] = {}

    @property
    def available(self) -> bool:
        return bool(self.providers)

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self.providers]

    def _ordered(self, preferred: str | None) -> list[LLMProvider]:
        now = time.monotonic()
        ordered = (
            sorted(self.providers, key=lambda p: p.name != preferred) if preferred else list(self.providers)
        )
        warm = [p for p in ordered if self._cooldown_until.get(p.name, 0) <= now]
        cold = [p for p in ordered if p not in warm]
        return warm + cold  # cooling providers are a last resort, never skipped entirely

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        purpose: str = "chat",
        **kwargs: Any,
    ) -> LLMResponse:
        """`model` may be "provider:model" (e.g. "groq:llama-3.3-70b-versatile") to pin a provider."""
        if not self.providers:
            raise LLMUnavailableError("No LLM provider configured (set GROQ_API_KEY and/or GEMINI_API_KEY)")
        preferred, pinned_model = None, None
        if model and ":" in model:
            preferred, pinned_model = model.split(":", 1)

        errors: list[str] = []
        for provider in self._ordered(preferred):
            if provider.name == preferred and pinned_model:
                use_model = pinned_model
            else:
                use_model = provider.nlu_model if purpose == "nlu" else provider.default_model
            try:
                response = await provider.chat(messages, model=use_model, **kwargs)
                self._cooldown_until.pop(provider.name, None)
                return response
            except LLMError as err:
                errors.append(f"{provider.name}: {err}")
                cooldown = _cooldown_for(err)
                if cooldown:
                    self._cooldown_until[provider.name] = time.monotonic() + cooldown
                log.warning(
                    "llm provider failed, trying next",
                    extra={"provider": provider.name, "status": err.status, "retryable": err.retryable},
                )
                if not err.retryable:
                    raise
        raise LLMUnavailableError("All LLM providers failed: " + " | ".join(errors)[:1000])


_router: LLMRouter | None = None


def build_default_router() -> LLMRouter:
    from app.agents.llm.openai_compat import OpenAICompatibleProvider

    providers: list[LLMProvider] = []
    if settings.groq_api_key.get_secret_value():
        providers.append(
            OpenAICompatibleProvider(
                name="groq",
                base_url=GROQ_BASE_URL,
                api_key=settings.groq_api_key.get_secret_value(),
                default_model=settings.groq_model,
                nlu_model=settings.groq_nlu_model,
                timeout=settings.llm_timeout_seconds,
            )
        )
    if settings.gemini_api_key.get_secret_value():
        providers.append(
            OpenAICompatibleProvider(
                name="gemini",
                base_url=GEMINI_BASE_URL,
                api_key=settings.gemini_api_key.get_secret_value(),
                default_model=settings.gemini_model,
                nlu_model=settings.gemini_nlu_model,
                timeout=settings.llm_timeout_seconds,
            )
        )
    if not providers:
        log.warning("No LLM API keys configured: agents run in rules-only mode (flows + scripted replies)")
    return LLMRouter(providers)


def get_llm_router() -> LLMRouter:
    global _router
    if _router is None:
        _router = build_default_router()
    return _router


def set_llm_router(router: LLMRouter | None) -> None:
    """Override the router (tests, simulation harness)."""
    global _router
    _router = router
