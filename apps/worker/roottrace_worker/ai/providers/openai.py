"""OpenAI, behind the `Provider` seam (`06` §2.2).

Structured output via `response_format={"type": "json_schema", "strict":
True}` — OpenAI's own schema-conforming mode. `strict` mode still leaves
`06` §4.1's ladder reachable: `additionalProperties`/`required` shape
mismatches OpenAI itself resolves before the model runs, but semantic rules
Pydantic enforces and JSON Schema cannot express (`ge`/`le` bounds compared
against another field, custom validators) still only get caught here."""

from __future__ import annotations

import openai

from roottrace_worker.ai.errors import ProviderError
from roottrace_worker.ai.providers.base import ProviderRequest, ProviderResponse


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        try:
            response = await self._client.chat.completions.create(
                model=request.model,
                max_tokens=request.max_tokens,
                messages=[
                    {"role": "system", "content": request.system},
                    {"role": "user", "content": request.user},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.schema_name,
                        "schema": request.json_schema,
                        "strict": True,
                    },
                },
                timeout=request.timeout_s,
            )
        except openai.RateLimitError as exc:
            raise ProviderError(self.name, "rate_limit", str(exc)) from exc
        except openai.APITimeoutError as exc:
            raise ProviderError(self.name, "timeout", str(exc)) from exc
        except openai.InternalServerError as exc:
            raise ProviderError(self.name, "server_error", str(exc)) from exc
        except openai.APIConnectionError as exc:
            raise ProviderError(self.name, "server_error", str(exc)) from exc

        choice = response.choices[0]
        if choice.finish_reason == "content_filter":
            raise ProviderError(self.name, "content_filter", "finish_reason=content_filter")
        content = choice.message.content
        if content is None:
            raise ProviderError(self.name, "content_filter", "empty message content")

        usage = response.usage
        cached = 0
        if usage is not None and usage.prompt_tokens_details is not None:
            cached = usage.prompt_tokens_details.cached_tokens or 0

        return ProviderResponse(
            raw_text=content,
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            cached_tokens_in=cached,
            model=response.model,
        )
