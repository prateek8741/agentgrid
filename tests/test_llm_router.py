import json

import httpx
import pytest

from app.agents.llm.base import LLMError, LLMUnavailableError
from app.agents.llm.openai_compat import OpenAICompatibleProvider
from app.agents.llm.router import LLMRouter
from app.agents.llm.scripted import ScriptedProvider, reply
from app.agents.tools import REGISTRY


def _provider(name: str, handler) -> OpenAICompatibleProvider:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatibleProvider(
        name=name,
        base_url=f"https://{name}.test/v1",
        api_key="k",
        default_model=f"{name}-big",
        nlu_model=f"{name}-small",
        client=client,
    )


def _completion(content=None, tool_calls=None, model="m"):  # type: ignore[no-untyped-def]
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7},
        },
    )


async def test_parses_text_and_usage() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return _completion("hello")

    resp = await _provider("groq", handler).chat([{"role": "user", "content": "hi"}], json_mode=True)
    assert resp.content == "hello" and resp.prompt_tokens == 11 and resp.completion_tokens == 7
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["body"]["model"] == "groq-big"


async def test_parses_tool_calls_and_bad_json_arguments() -> None:
    calls = [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "capture_lead", "arguments": '{"name": "Asha"}'},
        },
        {"id": "c2", "type": "function", "function": {"name": "capture_lead", "arguments": "{not json"}},
    ]
    resp = await _provider("groq", lambda r: _completion(tool_calls=calls)).chat(
        [{"role": "user", "content": "x"}]
    )
    assert resp.tool_calls[0].arguments == {"name": "Asha"} and resp.tool_calls[0].parse_error is None
    assert resp.tool_calls[1].arguments == {} and resp.tool_calls[1].parse_error


async def test_failover_on_429_then_cooldown_skips_provider() -> None:
    hits = {"groq": 0, "gemini": 0}

    def groq(_r: httpx.Request) -> httpx.Response:
        hits["groq"] += 1
        return httpx.Response(429, headers={"retry-after": "30"}, text="rate limited")

    def gemini(_r: httpx.Request) -> httpx.Response:
        hits["gemini"] += 1
        return _completion("from gemini")

    router = LLMRouter([_provider("groq", groq), _provider("gemini", gemini)])
    first = await router.chat([{"role": "user", "content": "hi"}])
    assert first.provider == "gemini" and first.content == "from gemini"
    await router.chat([{"role": "user", "content": "again"}])
    assert hits == {"groq": 1, "gemini": 2}  # groq is cooling down, not retried


@pytest.mark.parametrize("status", [500, 503, 401])
async def test_failover_on_server_and_auth_errors(status: int) -> None:
    router = LLMRouter(
        [
            _provider("groq", lambda r: httpx.Response(status, text="boom")),
            _provider("gemini", lambda r: _completion("ok")),
        ]
    )
    assert (await router.chat([{"role": "user", "content": "hi"}])).provider == "gemini"


async def test_transport_error_fails_over() -> None:
    def broken(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    router = LLMRouter([_provider("groq", broken), _provider("gemini", lambda r: _completion("ok"))])
    assert (await router.chat([{"role": "user", "content": "hi"}])).content == "ok"


async def test_tool_use_failed_400_is_retryable_but_plain_400_is_not() -> None:
    router = LLMRouter(
        [
            _provider("groq", lambda r: httpx.Response(400, text='{"error":{"code":"tool_use_failed"}}')),
            _provider("gemini", lambda r: _completion("ok")),
        ]
    )
    assert (await router.chat([{"role": "user", "content": "hi"}])).provider == "gemini"

    strict = LLMRouter(
        [
            _provider("groq", lambda r: httpx.Response(400, text="bad request")),
            _provider("gemini", lambda r: _completion("never")),
        ]
    )
    with pytest.raises(LLMError) as err:
        await strict.chat([{"role": "user", "content": "hi"}])
    assert err.value.status == 400


async def test_all_providers_down_and_no_providers() -> None:
    router = LLMRouter(
        [_provider("groq", lambda r: httpx.Response(503)), _provider("gemini", lambda r: httpx.Response(503))]
    )
    with pytest.raises(LLMUnavailableError):
        await router.chat([{"role": "user", "content": "hi"}])
    with pytest.raises(LLMUnavailableError):
        await LLMRouter([]).chat([{"role": "user", "content": "hi"}])
    assert not LLMRouter([]).available


async def test_pinned_provider_model_and_nlu_model_selection() -> None:
    a = ScriptedProvider(lambda c: reply("a"), name="groq", default_model="groq-big", nlu_model="groq-small")
    b = ScriptedProvider(lambda c: reply("b"), name="gemini", default_model="gem-big", nlu_model="gem-small")
    router = LLMRouter([a, b])
    await router.chat([{"role": "user", "content": "x"}], model="gemini:gem-pro")
    assert b.calls[-1].model == "gem-pro" and not a.calls
    await router.chat([{"role": "user", "content": "x"}], purpose="nlu")
    assert a.calls[-1].model == "groq-small"


def test_tool_schemas_are_portable() -> None:
    """No anyOf/null constructs (Gemini's OpenAI endpoint rejects them) and no tenant ids exposed."""
    for tool in REGISTRY.values():
        schema = json.dumps(tool.schema())
        assert "anyOf" not in schema and '"null"' not in schema, tool.name
        assert "tenant" not in schema.lower(), tool.name
