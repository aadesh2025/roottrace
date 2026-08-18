"""A scriptable `Provider`, for tests — the AI-gateway equivalent of
`tests/_fake_gateway.py`'s `FakeGateway`.

T5.1's own acceptance criterion is "*simulated* provider failure fails over
correctly" (`15` T5.1) — this is what does the simulating. Real network
calls to Anthropic/OpenAI are exercised by `providers/anthropic.py` and
`providers/openai.py` directly, in a live smoke test skipped unless real API
keys are present in the environment; everything about routing, failover,
retry, and the structured-output ladder is tested against this instead,
deterministically and offline.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from roottrace_worker.ai.contracts import FailoverTrigger
from roottrace_worker.ai.errors import ProviderError
from roottrace_worker.ai.providers.base import ProviderRequest, ProviderResponse


@dataclass(frozen=True, slots=True)
class ScriptedSuccess:
    raw_text: str
    tokens_in: int = 100
    tokens_out: int = 50
    cached_tokens_in: int = 0
    model: str = "fake-model"


@dataclass(frozen=True, slots=True)
class ScriptedFailure:
    trigger: FailoverTrigger
    detail: str = "simulated"


ScriptedOutcome = ScriptedSuccess | ScriptedFailure


@dataclass(slots=True)
class FakeProvider:
    """Each call consumes the next entry in `outcomes`, in order. Running
    out of entries is a test-authoring bug, not a provider failure, so it
    raises `IndexError` rather than silently repeating the last outcome —
    a test that expects three calls and gets a fourth should fail loudly."""

    name: str
    outcomes: Sequence[ScriptedOutcome]
    calls: list[ProviderRequest] = field(default_factory=list)
    _next: int = 0

    async def complete(self, request: ProviderRequest) -> ProviderResponse:
        self.calls.append(request)
        if self._next >= len(self.outcomes):
            raise IndexError(
                f"FakeProvider {self.name!r} received a call beyond its "
                f"{len(self.outcomes)} scripted outcome(s)"
            )
        outcome = self.outcomes[self._next]
        self._next += 1

        if isinstance(outcome, ScriptedFailure):
            raise ProviderError(self.name, outcome.trigger, outcome.detail)

        return ProviderResponse(
            raw_text=outcome.raw_text,
            tokens_in=outcome.tokens_in,
            tokens_out=outcome.tokens_out,
            cached_tokens_in=outcome.cached_tokens_in,
            model=outcome.model,
        )
