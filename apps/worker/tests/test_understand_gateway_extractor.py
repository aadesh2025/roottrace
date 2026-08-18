"""`GatewayExtractor` (T5.2) — the real `StructuredExtractor`, exercised
end-to-end through `understand(...)` with a `FakeProvider` standing in for
the model. This is what proves the prompt system and the gateway actually
compose: T4.1 built the seam, T5.1 built what fills it, T5.2 is the fill.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest

from roottrace_worker.ai.contracts import LLMCallRecord
from roottrace_worker.ai.gateway import LLMGateway
from roottrace_worker.ai.prompts.registry import load_prompt_registry
from roottrace_worker.ai.providers.fake import FakeProvider, ScriptedFailure, ScriptedSuccess
from roottrace_worker.ai.routing import parse_model_routing
from roottrace_worker.ai.storage import InMemoryObjectStore
from roottrace_worker.pipeline.understand import understand
from roottrace_worker.pipeline.understand.contracts import ExceptionFamily, Flag
from roottrace_worker.pipeline.understand.extractor import ExtractionRequest, ExtractorUnavailable
from roottrace_worker.pipeline.understand.gateway_extractor import GatewayExtractor

pytestmark = pytest.mark.unit

_PROMPTS = load_prompt_registry()

_EVENT = {
    "error": {
        "type": "TypeError",
        "message": "unsupported operand type(s) for +: 'NoneType' and 'int'",
    },
    "runtime": {"language": "python", "framework": "fastapi"},
    "stack_trace": (
        'File "/app/services/checkout.py", line 142, in calculate_total\n'
        "    total = base_price + tax_amount\n"
        'File "/app/api/routes/checkout.py", line 58, in create_checkout\n'
        "    total = calculate_total(cart)\n"
    ),
    "breadcrumbs": [
        {"ts": "2026-08-04T09:14:22.200Z", "message": "GET tax-service/rate -> 503"},
    ],
    "request": {"method": "POST", "route": "/api/v2/checkout"},
}

_VALID_REPLY = json.dumps(
    {
        "exception": {"family": "integration"},
        "implicated_symbols": ["calculate_total", "get_rate"],
        "initial_hypotheses": [
            {
                "statement": "tax_amount is None because the tax client swallowed a 503",
                "prior": 0.7,
                "evidence_needed": [],
            }
        ],
        "retrieval_plan": {
            "must_fetch": ["clients/tax_client.py"],
            "breadcrumb_signal": "GET tax-service/rate -> 503",
        },
        "notes": "The breadcrumb is the strongest signal here.",
        "extraction_confidence": 0.9,
    }
)


def _routing_doc() -> dict[str, object]:
    return {
        "tiers": {
            "fast": [{"provider": "anthropic", "model": "claude-haiku-4-5"}],
            "reasoning-a": [{"provider": "anthropic", "model": "claude-sonnet-5"}],
            "reasoning-b": [{"provider": "anthropic", "model": "claude-sonnet-5"}],
            "embed": [{"provider": "voyage", "model": "voyage-code-3", "dimensions": 1536}],
        },
        "failover": {
            "trigger_on": ["rate_limit", "timeout", "server_error", "content_filter"],
            "max_provider_attempts": 1,
            "backoff": {"base_ms": 1, "factor": 1, "jitter": False, "max_ms": 1},
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


def _extractor(
    provider: FakeProvider, *, db: InMemoryLLMCallsRepository | None = None
) -> GatewayExtractor:
    gateway = LLMGateway(
        providers={"anthropic": provider},
        routing=parse_model_routing(_routing_doc()),
        prompts=_PROMPTS,
        storage=InMemoryObjectStore(),
        db=db or InMemoryLLMCallsRepository(),
        sleep=_noop_sleep,
    )
    return GatewayExtractor(gateway=gateway, prompts=_PROMPTS, project_id="proj_1")


async def test_a_successful_extraction_merges_onto_the_deterministic_pre_parse() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(_VALID_REPLY)])
    outcome = await understand(_EVENT, extractor=_extractor(provider))

    assert outcome.extraction_performed is True
    assert outcome.understanding.exception.family.value == "integration"
    assert "get_rate" in outcome.understanding.implicated_symbols
    assert "clients/tax_client.py" in outcome.understanding.retrieval_plan.must_fetch
    assert outcome.understanding.extraction_confidence == pytest.approx(0.9)


async def test_the_call_reaches_the_provider_with_the_assembled_prompt() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(_VALID_REPLY)])
    await understand(_EVENT, extractor=_extractor(provider))

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert "EXCEPTION TAXONOMY" in call.system
    assert "RETRIEVAL PLAN" in call.system  # understand/v3.md's task text
    assert "<untrusted_context>" in call.user
    assert "tax-service/rate" in call.user  # the breadcrumb, fenced as data


async def test_a_provider_failure_falls_back_to_the_deterministic_pre_parse() -> None:
    """`03` §S4: never terminal. `ExtractorUnavailable` must not propagate
    out of `understand`."""
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedFailure("server_error")])
    outcome = await understand(_EVENT, extractor=_extractor(provider))

    assert outcome.extraction_performed is False
    assert outcome.understanding.extraction_confidence == 0.5
    assert Flag.DETERMINISTIC_ONLY in outcome.understanding.flags


async def test_gateway_errors_are_translated_to_extractor_unavailable_directly() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedFailure("server_error")])
    extractor = _extractor(provider)

    with pytest.raises(ExtractorUnavailable):
        await extractor.extract(
            ExtractionRequest(
                language="python",
                framework=None,
                exception_type="TypeError",
                exception_message="x",
                family=ExceptionFamily.UNCLASSIFIED,
                frames=(),
            )
        )


async def test_every_call_is_billed_and_recorded_even_for_the_extraction_seam() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(_VALID_REPLY)])
    db = InMemoryLLMCallsRepository()
    await understand(_EVENT, extractor=_extractor(provider, db=db))

    assert len(db.records) == 1
    assert db.records[0].stage == "understand"
    assert db.records[0].tier == "fast"


async def test_all_providers_exhausted_still_falls_back_cleanly() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedFailure("timeout")])
    outcome = await understand(_EVENT, extractor=_extractor(provider))
    # AllProvidersExhaustedError is an LLMError, caught the same way.
    assert outcome.extraction_performed is False
