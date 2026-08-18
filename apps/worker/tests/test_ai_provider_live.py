"""Live smoke tests against the real Anthropic/OpenAI APIs.

Skipped unless the real key is present in the environment — `ADR-007`/
`06` §2.4 note that LLM providers are the one real external dependency V1
still has (unlike GitHub, which runs in `fixture` mode throughout V1), but a
live network call has no place in the suite `make check` runs on every
commit. This file exists so a human (or CI with real keys configured) can
still confirm the two providers work against the real APIs, not just
against the mocked SDK client in `test_ai_provider_anthropic.py` /
`test_ai_provider_openai.py`."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest
from pydantic import BaseModel

from roottrace_worker.ai.providers.anthropic import AnthropicProvider
from roottrace_worker.ai.providers.base import ProviderRequest
from roottrace_worker.ai.providers.openai import OpenAIProvider
from roottrace_worker.ai.structured import parse_and_validate

pytestmark = pytest.mark.integration


class Greeting(BaseModel):
    message: str


def _request() -> ProviderRequest:
    return ProviderRequest(
        model="",  # filled in per-test below
        system="You respond only with the requested structured output.",
        user="Say hello.",
        json_schema=Greeting.model_json_schema(),
        schema_name="Greeting",
        max_tokens=256,
        timeout_s=30,
    )


@pytest.mark.skipif(
    not os.environ.get("RT_ANTHROPIC_API_KEY"), reason="RT_ANTHROPIC_API_KEY not set"
)
async def test_anthropic_returns_valid_structured_output() -> None:
    provider = AnthropicProvider(api_key=os.environ["RT_ANTHROPIC_API_KEY"])
    request = replace(_request(), model="claude-haiku-4-5")

    response = await provider.complete(request)

    result = parse_and_validate(response.raw_text, Greeting)
    assert result.ok
    assert response.tokens_in > 0
    assert response.tokens_out > 0


@pytest.mark.skipif(not os.environ.get("RT_OPENAI_API_KEY"), reason="RT_OPENAI_API_KEY not set")
async def test_openai_returns_valid_structured_output() -> None:
    provider = OpenAIProvider(api_key=os.environ["RT_OPENAI_API_KEY"])
    request = replace(_request(), model="gpt-4.1-mini")

    response = await provider.complete(request)

    result = parse_and_validate(response.raw_text, Greeting)
    assert result.ok
    assert response.tokens_in > 0
    assert response.tokens_out > 0
