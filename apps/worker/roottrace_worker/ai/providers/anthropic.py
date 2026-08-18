"""Anthropic, behind the `Provider` seam (`06` §2.2).

Structured output via forced tool use — the model has exactly one tool
(named after the schema) and `tool_choice` forces it, so the model cannot
answer in prose. `06` §4.1's ladder still runs on top of this: forcing tool
use makes attempt 1 succeed far more often, it does not make attempt 2/3
unreachable, since a model can still return a tool call whose `input`
disagrees with the schema in ways only Pydantic validation catches (an
enum value outside the allowed set, a missing required field the tool
schema itself did not enforce as tightly as Pydantic does).
"""

from __future__ import annotations

import json

import anthropic

from roottrace_worker.ai.errors import ProviderError
from roottrace_worker.ai.providers.base import ProviderRequest, ProviderResponse


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        try:
            response = await self._client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[{"role": "user", "content": request.user}],
                tools=[
                    {
                        "name": request.schema_name,
                        "input_schema": request.json_schema,
                    }
                ],
                tool_choice={"type": "tool", "name": request.schema_name},
                timeout=request.timeout_s,
            )
        except anthropic.RateLimitError as exc:
            raise ProviderError(self.name, "rate_limit", str(exc)) from exc
        except anthropic.APITimeoutError as exc:
            raise ProviderError(self.name, "timeout", str(exc)) from exc
        except (anthropic.InternalServerError, anthropic.OverloadedError) as exc:
            raise ProviderError(self.name, "server_error", str(exc)) from exc
        except anthropic.APIConnectionError as exc:
            raise ProviderError(self.name, "server_error", str(exc)) from exc

        tool_use = next((block for block in response.content if block.type == "tool_use"), None)
        if tool_use is None:
            # The model answered in prose despite `tool_choice` forcing the
            # tool — treated as a content-filter-shaped failure (the model
            # refused the task) rather than a schema failure the repair
            # ladder could fix, since there is no JSON here to repair.
            raise ProviderError(self.name, "content_filter", "no tool_use block in response")

        usage = response.usage
        return ProviderResponse(
            raw_text=json.dumps(tool_use.input),
            tokens_in=usage.input_tokens,
            tokens_out=usage.output_tokens,
            cached_tokens_in=getattr(usage, "cache_read_input_tokens", None) or 0,
            model=response.model,
        )
