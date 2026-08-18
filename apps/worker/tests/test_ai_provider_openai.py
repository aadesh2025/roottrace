"""`OpenAIProvider` (`06` §2.2, T5.1) — same approach as
`test_ai_provider_anthropic.py`: the SDK's async client is monkeypatched at
`provider._client.chat.completions.create`, no network call."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import openai
import pytest

from roottrace_worker.ai.errors import ProviderError
from roottrace_worker.ai.providers.base import ProviderRequest
from roottrace_worker.ai.providers.openai import OpenAIProvider

pytestmark = pytest.mark.unit

VALID_JSON = '{"root_cause": "x", "confidence": 0.5}'


def _request() -> ProviderRequest:
    return ProviderRequest(
        model="gpt-5",
        system="s",
        user="u",
        json_schema={"type": "object"},
        schema_name="Verdict",
        max_tokens=1024,
        timeout_s=30,
    )


def _httpx_request() -> httpx.Request:
    return httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _chat_response(
    content: str | None, *, finish_reason: str = "stop", cached_tokens: int | None = 3
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=content))
        ],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens)
            if cached_tokens is not None
            else None,
        ),
        model="gpt-5-2026-08-01",
    )


async def test_a_successful_call_returns_the_message_content_as_raw_json() -> None:
    provider = OpenAIProvider(api_key="test-key")
    provider._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_chat_response(VALID_JSON)
    )

    response = await provider.complete(_request())

    assert response.raw_text == VALID_JSON
    assert response.tokens_in == 100
    assert response.tokens_out == 20
    assert response.cached_tokens_in == 3
    assert response.model == "gpt-5-2026-08-01"


async def test_a_missing_cached_tokens_detail_defaults_to_zero() -> None:
    provider = OpenAIProvider(api_key="test-key")
    provider._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_chat_response(VALID_JSON, cached_tokens=None)
    )

    response = await provider.complete(_request())
    assert response.cached_tokens_in == 0


async def test_a_content_filter_finish_reason_is_a_content_filter_failure() -> None:
    provider = OpenAIProvider(api_key="test-key")
    provider._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_chat_response(None, finish_reason="content_filter")
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.complete(_request())
    assert exc_info.value.trigger == "content_filter"


async def test_empty_message_content_is_a_content_filter_failure() -> None:
    provider = OpenAIProvider(api_key="test-key")
    provider._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        return_value=_chat_response(None)
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.complete(_request())
    assert exc_info.value.trigger == "content_filter"


async def test_rate_limit_error_maps_to_the_rate_limit_trigger() -> None:
    provider = OpenAIProvider(api_key="test-key")
    response = httpx.Response(429, request=_httpx_request())
    provider._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=openai.RateLimitError(message="rate limited", response=response, body=None)
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.complete(_request())
    assert exc_info.value.trigger == "rate_limit"


async def test_timeout_error_maps_to_the_timeout_trigger() -> None:
    provider = OpenAIProvider(api_key="test-key")
    provider._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=openai.APITimeoutError(request=_httpx_request())
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.complete(_request())
    assert exc_info.value.trigger == "timeout"


async def test_internal_server_error_maps_to_the_server_error_trigger() -> None:
    provider = OpenAIProvider(api_key="test-key")
    response = httpx.Response(500, request=_httpx_request())
    provider._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=openai.InternalServerError(message="oops", response=response, body=None)
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.complete(_request())
    assert exc_info.value.trigger == "server_error"


async def test_connection_error_maps_to_the_server_error_trigger() -> None:
    provider = OpenAIProvider(api_key="test-key")
    provider._client.chat.completions.create = AsyncMock(  # type: ignore[method-assign]
        side_effect=openai.APIConnectionError(request=_httpx_request())
    )

    with pytest.raises(ProviderError) as exc_info:
        await provider.complete(_request())
    assert exc_info.value.trigger == "server_error"


async def test_the_provider_calls_with_the_requested_model_and_strict_schema() -> None:
    provider = OpenAIProvider(api_key="test-key")
    create = AsyncMock(return_value=_chat_response(VALID_JSON))
    provider._client.chat.completions.create = create  # type: ignore[method-assign]

    await provider.complete(_request())

    _, kwargs = create.call_args
    assert kwargs["model"] == "gpt-5"
    assert kwargs["response_format"]["json_schema"]["name"] == "Verdict"
    assert kwargs["response_format"]["json_schema"]["strict"] is True
