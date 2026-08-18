"""The provider seam (`06` §2.4) — same shape as `github/gateway.py`'s
`GitHubGateway`: a `Protocol`, satisfied structurally, so a test double is a
real implementation of the contract rather than a subclass standing in for
one.

**No orchestration code below `gateway.py` may branch on which concrete
provider it is talking to.** Model-specific request shaping (tool-use vs.
`response_format=json_schema`, message formatting) lives entirely inside
each provider's own `complete`; everything above this seam sees one
`ProviderResponse` shape regardless of vendor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    """What a provider needs to make one call. `json_schema` is the
    `output_model`'s schema, already rendered to a JSON Schema dict by the
    caller — providers accept a schema, they do not derive one from a
    Pydantic model, which would make every provider implementation depend on
    `pydantic` internals rather than a plain dict."""

    model: str
    system: str
    user: str
    json_schema: dict[str, object]
    schema_name: str
    max_tokens: int
    timeout_s: float


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """One successful call. `raw_text` is the unparsed JSON the model
    returned — `structured.py`'s ladder is what turns this into a validated
    Pydantic instance, deliberately kept out of the provider's own
    responsibility so every provider's parsing failure is handled by one
    piece of code, not N slightly-different ones."""

    raw_text: str
    tokens_in: int
    tokens_out: int
    cached_tokens_in: int
    model: str


@runtime_checkable
class Provider(Protocol):
    """One model provider (Anthropic, OpenAI, ...). Everything `06` §2.2's
    tier list needs from a concrete provider."""

    #: The routing config's `provider:` key (`"anthropic"`, `"openai"`, ...)
    #: — used for `llm_calls.provider`, log lines, and matching a tier
    #: entry back to the object that should serve it.
    name: str

    async def complete(self, request: ProviderRequest) -> ProviderResponse: ...
