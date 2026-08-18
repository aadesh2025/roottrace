"""`LLMGateway.complete` (`06` §2.4, T5.1) — the orchestration tests behind
`15` T5.1's own three accept criteria:

> Simulated provider failure fails over correctly. Malformed JSON triggers
> a repair call on the cheap tier. Every call writes an `llm_calls` row
> with exact tokens and cost.

Built entirely against `FakeProvider` and in-memory doubles for storage,
cache, breaker, and `llm_calls` persistence — real network calls are
`providers/anthropic.py` and `providers/openai.py`'s own concern, exercised
separately (a live smoke test, skipped without real API keys)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel

from roottrace_worker.ai.contracts import CompletionRequest, LLMCallRecord, RenderedPrompt
from roottrace_worker.ai.errors import (
    AllProvidersExhaustedError,
    ProviderError,
    QuotaExhaustedError,
    SchemaValidationFailedError,
    SuspiciousContentRejectedError,
)
from roottrace_worker.ai.gateway import LLMGateway
from roottrace_worker.ai.providers.fake import FakeProvider, ScriptedFailure, ScriptedSuccess
from roottrace_worker.ai.routing import parse_model_routing
from roottrace_worker.ai.storage import InMemoryObjectStore

pytestmark = pytest.mark.unit


class Verdict(BaseModel):
    root_cause: str
    confidence: float


VALID_JSON = '{"root_cause": "null deref", "confidence": 0.8}'


def _routing_doc(
    *, max_provider_attempts: int = 2, trigger_on: tuple[str, ...] | None = None
) -> dict[str, Any]:
    return {
        "tiers": {
            "fast": [
                {"provider": "openai", "model": "gpt-4.1-mini"},
                {"provider": "anthropic", "model": "claude-haiku-4-5"},
            ],
            "reasoning-a": [
                {"provider": "anthropic", "model": "claude-sonnet-5"},
                {"provider": "openai", "model": "gpt-5"},
            ],
            "reasoning-b": [{"provider": "openai", "model": "gpt-5"}],
            "embed": [{"provider": "voyage", "model": "voyage-code-3", "dimensions": 1536}],
        },
        "failover": {
            "trigger_on": list(
                trigger_on or ("rate_limit", "timeout", "server_error", "content_filter")
            ),
            "max_provider_attempts": max_provider_attempts,
            "backoff": {"base_ms": 1000, "factor": 2, "jitter": False, "max_ms": 16000},
        },
    }


@dataclass(slots=True)
class InMemoryLLMCallsRepository:
    records: list[LLMCallRecord] = field(default_factory=list)

    async def insert(self, record: LLMCallRecord) -> str:
        self.records.append(record)
        return f"call_{len(self.records)}"


async def _noop_sleep(_seconds: float) -> None:
    return None


def _request(**overrides: object) -> CompletionRequest[Verdict]:
    base: dict[str, object] = {
        "tier": "reasoning-a",
        "prompt": RenderedPrompt(system="s", user="u", contains_untrusted_content=False),
        "output_model": Verdict,
        "project_id": "proj_1",
        "stage": "reason",
        "prompt_version": "v3",
        "now": datetime(2026, 8, 18, tzinfo=UTC),
    }
    base.update(overrides)
    return CompletionRequest(**base)  # type: ignore[arg-type]


def _gateway(
    *,
    providers: dict[str, FakeProvider],
    routing_doc: dict[str, Any] | None = None,
    **overrides: object,
) -> LLMGateway:
    kwargs: dict[str, object] = {
        "providers": providers,
        "routing": parse_model_routing(routing_doc or _routing_doc()),
        "storage": InMemoryObjectStore(),
        "db": InMemoryLLMCallsRepository(),
        "sleep": _noop_sleep,
        "clock": _make_clock(),
    }
    kwargs.update(overrides)
    return LLMGateway(**kwargs)  # type: ignore[arg-type]


def _make_clock() -> object:
    ticks = iter(range(0, 100_000))

    def clock() -> float:
        return next(ticks) / 1000

    return clock


# ── Accept criterion 1: simulated provider failure fails over ───────────


async def test_a_rate_limited_primary_provider_fails_over_to_the_next() -> None:
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedFailure("rate_limit")])
    openai = FakeProvider(name="openai", outcomes=[ScriptedSuccess(VALID_JSON)])
    db = InMemoryLLMCallsRepository()
    gateway = _gateway(
        providers={"anthropic": anthropic, "openai": openai},
        db=db,
        routing_doc=_routing_doc(max_provider_attempts=1),
    )

    result = await gateway.complete(_request())

    assert result.provider == "openai"
    assert result.output.root_cause == "null deref"
    assert len(db.records) == 1
    assert db.records[0].provider == "openai"
    assert db.records[0].failover_from == "anthropic"


async def test_every_provider_in_the_tier_failing_raises_all_providers_exhausted() -> None:
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedFailure("rate_limit")] * 2)
    openai = FakeProvider(name="openai", outcomes=[ScriptedFailure("server_error")] * 2)
    gateway = _gateway(providers={"anthropic": anthropic, "openai": openai})

    with pytest.raises(AllProvidersExhaustedError):
        await gateway.complete(_request())


async def test_a_pure_failure_writes_no_llm_calls_row() -> None:
    """Nothing was billed, so there is nothing to attribute — only a
    response that actually came back gets a row."""
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedFailure("rate_limit")] * 2)
    openai = FakeProvider(name="openai", outcomes=[ScriptedFailure("server_error")] * 2)
    db = InMemoryLLMCallsRepository()
    gateway = _gateway(providers={"anthropic": anthropic, "openai": openai}, db=db)

    with pytest.raises(AllProvidersExhaustedError):
        await gateway.complete(_request())
    assert db.records == []


async def test_a_provider_gets_max_provider_attempts_before_failing_over() -> None:
    anthropic = FakeProvider(
        name="anthropic", outcomes=[ScriptedFailure("rate_limit"), ScriptedSuccess(VALID_JSON)]
    )
    openai = FakeProvider(name="openai", outcomes=[ScriptedSuccess(VALID_JSON)])
    gateway = _gateway(providers={"anthropic": anthropic, "openai": openai})

    result = await gateway.complete(_request())

    assert result.provider == "anthropic"
    assert len(anthropic.calls) == 2
    assert len(openai.calls) == 0  # never needed


async def test_an_unconfigured_failover_trigger_is_not_retried() -> None:
    """`06` §2.2's `trigger_on` list is a configuration decision — a trigger
    not in it must propagate immediately, not consume a provider attempt or
    reach the next provider in the tier."""
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedFailure("content_filter")])
    openai = FakeProvider(name="openai", outcomes=[ScriptedSuccess(VALID_JSON)])
    gateway = _gateway(
        providers={"anthropic": anthropic, "openai": openai},
        routing_doc=_routing_doc(trigger_on=("rate_limit", "timeout", "server_error")),
    )

    with pytest.raises(ProviderError, match="content_filter"):
        await gateway.complete(_request())
    assert len(openai.calls) == 0


# ── Accept criterion 2: malformed JSON triggers a repair call ───────────


async def test_malformed_json_triggers_a_repair_call_on_the_fast_tier() -> None:
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess("not json at all")])
    openai = FakeProvider(name="openai", outcomes=[ScriptedSuccess(VALID_JSON)])
    db = InMemoryLLMCallsRepository()
    gateway = _gateway(providers={"anthropic": anthropic, "openai": openai}, db=db)

    result = await gateway.complete(_request())

    assert result.schema_repair_used is True
    assert result.output.root_cause == "null deref"
    # openai leads the `fast` tier in `_routing_doc` — the repair call must
    # have gone there, not to whichever provider led the original tier.
    assert len(openai.calls) == 1
    assert len(db.records) == 2
    assert db.records[0].attempt == 1
    assert db.records[1].attempt == 2
    assert db.records[1].tier == "fast"


async def test_the_repair_prompt_carries_the_original_response_and_the_validator_error() -> None:
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess("not json at all")])
    openai = FakeProvider(name="openai", outcomes=[ScriptedSuccess(VALID_JSON)])
    gateway = _gateway(providers={"anthropic": anthropic, "openai": openai})

    await gateway.complete(_request())

    repair_call = openai.calls[0]
    assert "not json at all" in repair_call.user
    assert "invalid JSON" in repair_call.user


async def test_a_repair_call_that_is_salvageable_succeeds_without_a_third_call() -> None:
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess("garbage")])
    openai = FakeProvider(
        name="openai", outcomes=[ScriptedSuccess(f"Here you go: {VALID_JSON} thanks!")]
    )
    db = InMemoryLLMCallsRepository()
    gateway = _gateway(providers={"anthropic": anthropic, "openai": openai}, db=db)

    result = await gateway.complete(_request())

    assert result.output.root_cause == "null deref"
    assert result.schema_repair_used is True
    # Salvage makes no call of its own.
    assert len(db.records) == 2


async def test_total_schema_failure_across_all_three_attempts_raises() -> None:
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess("garbage")])
    openai = FakeProvider(name="openai", outcomes=[ScriptedSuccess("still not json")])
    db = InMemoryLLMCallsRepository()
    gateway = _gateway(providers={"anthropic": anthropic, "openai": openai}, db=db)

    with pytest.raises(SchemaValidationFailedError):
        await gateway.complete(_request())
    # The native and repair calls were both real, billed round-trips, so
    # both still get an `llm_calls` row even though the stage ultimately fails.
    assert len(db.records) == 2


# ── Accept criterion 3: every call writes an llm_calls row with exact tokens/cost ─


async def test_the_llm_calls_row_carries_exact_tokens_and_cost() -> None:
    anthropic = FakeProvider(
        name="anthropic",
        outcomes=[ScriptedSuccess(VALID_JSON, tokens_in=19_000, tokens_out=2_100)],
    )
    db = InMemoryLLMCallsRepository()
    gateway = _gateway(
        providers={"anthropic": anthropic, "openai": FakeProvider("openai", [])}, db=db
    )

    result = await gateway.complete(_request())

    assert result.tokens_in == 19_000
    assert result.tokens_out == 2_100
    # claude-sonnet-5: 3000/1000 in, 15000/1000 out (see test_ai_cost.py)
    assert result.cost_micro_usd == 19_000 * 3 + 2_100 * 15
    assert db.records[0].tokens_in == 19_000
    assert db.records[0].tokens_out == 2_100
    assert db.records[0].cost_micro_usd == result.cost_micro_usd


async def test_a_result_that_failed_over_records_which_provider_it_failed_over_from() -> None:
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedFailure("timeout")])
    openai = FakeProvider(name="openai", outcomes=[ScriptedSuccess(VALID_JSON)])
    gateway = _gateway(
        providers={"anthropic": anthropic, "openai": openai},
        routing_doc=_routing_doc(max_provider_attempts=1),
    )

    result = await gateway.complete(_request())
    assert result.failover_from == "anthropic"


# ── Redaction ─────────────────────────────────────────────────────────


async def test_a_secret_in_the_prompt_is_redacted_before_reaching_the_provider() -> None:
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_JSON)])
    gateway = _gateway(providers={"anthropic": anthropic, "openai": FakeProvider("openai", [])})

    secret = f"gh{'p'}_{'a' * 36}"
    request = _request(
        prompt=RenderedPrompt(
            system="s", user=f"the token is {secret}", contains_untrusted_content=True
        )
    )
    await gateway.complete(request)

    assert secret not in anthropic.calls[0].user
    assert "[REDACTED:github_token]" in anthropic.calls[0].user


# ── Suspicious content (`06` §3.2) ───────────────────────────────────────


async def test_a_response_echoing_a_flagged_injection_pattern_is_rejected() -> None:
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_JSON)])
    gateway = _gateway(providers={"anthropic": anthropic, "openai": FakeProvider("openai", [])})

    request = _request(
        prompt=RenderedPrompt(
            system="s",
            user="u",
            contains_untrusted_content=True,
            flagged_injection_patterns=("null deref",),
        )
    )
    with pytest.raises(SuspiciousContentRejectedError):
        await gateway.complete(request)


# ── Deterministic caching (`06` §2.4) ────────────────────────────────────


class InMemoryRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, name: str) -> str | None:
        return self._store.get(name)

    async def set(self, name: str, value: str, *, ex: int | None = None) -> bool:
        self._store[name] = value
        return True

    async def incrby(self, name: str, amount: int) -> int:
        current = int(self._store.get(name, "0")) + amount
        self._store[name] = str(current)
        return current

    async def decrby(self, name: str, amount: int) -> int:
        current = int(self._store.get(name, "0")) - amount
        self._store[name] = str(current)
        return current

    async def expire(self, name: str, seconds: int) -> bool:
        return True


async def test_an_identical_deterministic_call_is_served_from_cache() -> None:
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_JSON)])
    redis = InMemoryRedis()
    gateway = _gateway(
        providers={"anthropic": anthropic, "openai": FakeProvider("openai", [])},
        cache_redis=redis,
    )
    request = _request(deterministic=True)

    first = await gateway.complete(request)
    second = await gateway.complete(request)

    assert len(anthropic.calls) == 1  # the second call never reached the provider
    assert second.output.root_cause == first.output.root_cause
    assert second.cost_micro_usd == 0  # already billed the first time


async def test_a_non_deterministic_call_never_reads_the_cache() -> None:
    anthropic = FakeProvider(
        name="anthropic", outcomes=[ScriptedSuccess(VALID_JSON), ScriptedSuccess(VALID_JSON)]
    )
    redis = InMemoryRedis()
    gateway = _gateway(
        providers={"anthropic": anthropic, "openai": FakeProvider("openai", [])},
        cache_redis=redis,
    )
    request = _request(deterministic=False)

    await gateway.complete(request)
    await gateway.complete(request)

    assert len(anthropic.calls) == 2


# ── Circuit breaker (`06` §8.2a, B9) ─────────────────────────────────────


async def test_a_reservation_over_the_daily_cap_blocks_before_any_provider_call() -> None:
    anthropic = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_JSON)])
    redis = InMemoryRedis()
    gateway = _gateway(
        providers={"anthropic": anthropic, "openai": FakeProvider("openai", [])},
        breaker_redis=redis,
        daily_cap_micro_usd=100,
        reservation_estimate_micro_usd=1_000,
    )

    with pytest.raises(QuotaExhaustedError):
        await gateway.complete(_request())
    assert anthropic.calls == []


async def test_the_reservation_is_reconciled_to_the_actual_cost_on_success() -> None:
    anthropic = FakeProvider(
        name="anthropic",
        outcomes=[ScriptedSuccess(VALID_JSON, tokens_in=1_000, tokens_out=1_000)],
    )
    redis = InMemoryRedis()
    gateway = _gateway(
        providers={"anthropic": anthropic, "openai": FakeProvider("openai", [])},
        breaker_redis=redis,
        daily_cap_micro_usd=5_000_000,
        reservation_estimate_micro_usd=420_000,
    )

    result = await gateway.complete(_request())

    stored = await redis.get("rt:cost:proj_1:2026-08-18")
    assert stored is not None
    assert int(stored) == result.cost_micro_usd
