"""`AnthropicProvider` (`06` §2.2, T5.1) — the SDK's own async client is
monkeypatched at `provider._client.messages.create`, so these tests exercise
real request/response shaping and exception-mapping without a network call.
A live smoke test against the real API lives in
`test_ai_provider_live.py`, skipped unless `RT_ANTHROPIC_API_KEY` is set."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import anthropic
import httpx
import pytest

from roottrace_worker.ai.errors import ProviderError
from roottrace_worker.ai.providers.anthropic import AnthropicProvider
from roottrace_worker.ai.providers.base import ProviderRequest

pytestmark = pytest.mark.unit


def _request() -> ProviderRequest:
    return ProviderRequest(
        model="claude-sonnet-5",
        system="s",
        user="u",
        json_schema={"type": "object"},
        schema_name="Verdict",
        max_tokens=1024,
        timeout_s=30,
    )


def _httpx_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _tool_use_response(
    input_dict: dict[str, object], *, cache_read: int | None = 5
) -> SimpleNamespace:
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", input=input_dict)],
        usage=SimpleNamespace(
            input_tokens=100, output_tokens=20, cache_read_input_tokens=cache_read
        ),
        model="claude-sonnet-5-20260501",
    )


async def test_a_successful_tool_use_call_returns_the_input_as_raw_json() -> None:
    provider = AnthropicProvider(api_key="test-key")
    provider._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_tool_use_response({"root_cause": "x", "confidence": 0.5})
    )

    response = await provider.complete(_request())

    assert json.loads(response.raw_text) == {"root_cause": "x", "confidence": 0.5}
    assert response.tokens_in == 100
    assert response.tokens_out == 20
    assert response.cached_tokens_in == 5
    assert response.model == "claude-sonnet-5-20260501"


async def test_a_missing_cache_read_count_defaults_to_zero() -> None:
    provider = AnthropicProvider(api_key="test-key")
    provider._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_tool_use_response({"root_cause": "x", "confidence": 0.5}, cache_read=None)
    )

    response = await provider.complete(_request())
    assert response.cached_tokens_in == 0


async def test_no_tool_use_block_is_a_content_filter_failure() -> None:
    provider = AnthropicProvider(api_key="test-key")
    provider._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(
            content=[SimpleNamespace(type="text", text="I cannot help with that.")],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0),
            model="claude-sonnet-5-20260501",
        )
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.complete(_request())
    assert exc_info.value.trigger == "content_filter"


async def test_rate_limit_error_maps_to_the_rate_limit_trigger() -> None:
    provider = AnthropicProvider(api_key="test-key")
    response = httpx.Response(429, request=_httpx_request())
    provider._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=anthropic.RateLimitError(message="rate limited", response=response, body=None)
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.complete(_request())
    assert exc_info.value.trigger == "rate_limit"


async def test_timeout_error_maps_to_the_timeout_trigger() -> None:
    provider = AnthropicProvider(api_key="test-key")
    provider._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=anthropic.APITimeoutError(request=_httpx_request())
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.complete(_request())
    assert exc_info.value.trigger == "timeout"


async def test_internal_server_error_maps_to_the_server_error_trigger() -> None:
    provider = AnthropicProvider(api_key="test-key")
    response = httpx.Response(500, request=_httpx_request())
    provider._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=anthropic.InternalServerError(message="oops", response=response, body=None)
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.complete(_request())
    assert exc_info.value.trigger == "server_error"


async def test_connection_error_maps_to_the_server_error_trigger() -> None:
    provider = AnthropicProvider(api_key="test-key")
    provider._client.messages.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=anthropic.APIConnectionError(request=_httpx_request())
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.complete(_request())
    assert exc_info.value.trigger == "server_error"


async def test_the_provider_calls_with_the_requested_model_and_tool_choice() -> None:
    provider = AnthropicProvider(api_key="test-key")
    create = AsyncMock(return_value=_tool_use_response({"root_cause": "x", "confidence": 0.5}))
    provider._client.messages.create = create  # type: ignore[method-assign]

    await provider.complete(_request())

    _, kwargs = create.call_args
    assert kwargs["model"] == "claude-sonnet-5"
    assert kwargs["tool_choice"] == {"type": "tool", "name": "Verdict"}
    assert kwargs["tools"][0]["name"] == "Verdict"
