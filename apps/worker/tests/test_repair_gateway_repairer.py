"""`GatewayRepairer` (T7.1) — the real `StructuredRepairer`, exercised
end-to-end through `repair(...)` with a `FakeProvider` standing in for the
model. Mirrors `test_patch_gateway_patcher.py`'s shape — `fast` tier, one
call, no bespoke correction-retry ladder (`gateway_repairer.py`'s module
docstring explains why S9 doesn't need one)."""

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
from roottrace_worker.pipeline.patch.contracts import Patch, RiskAssessment
from roottrace_worker.pipeline.reason.contracts import FixStrategy, RootCause, RootCauseAnalysis
from roottrace_worker.pipeline.repair.contracts import PreviousAttempt
from roottrace_worker.pipeline.repair.gateway_repairer import GatewayRepairer
from roottrace_worker.pipeline.repair.repairer import RepairerUnavailable, RepairRequest
from roottrace_worker.pipeline.understand.contracts import (
    ErrorUnderstanding,
    ExceptionFamily,
    ExceptionInfo,
    RetrievalPlan,
)

pytestmark = pytest.mark.unit

_PROMPTS = load_prompt_registry()

VALID_REPLY = json.dumps(
    {
        "instruction_delta": "Keep the typed exception, but update "
        "tests/test_quote.py to assert the new behaviour."
    }
)


def _request(
    *, failed_gate: str = "G6", previous_attempts: tuple[PreviousAttempt, ...] = ()
) -> RepairRequest:
    return RepairRequest(
        understanding=ErrorUnderstanding(
            exception=ExceptionInfo(
                type="TaxServiceUnavailable",
                family=ExceptionFamily.INTEGRATION,
                message_normalized="tax service unavailable",
                is_user_facing=True,
            ),
            retrieval_plan=RetrievalPlan(),
            extraction_confidence=0.9,
        ),
        root_cause=RootCauseAnalysis(
            root_cause=RootCause(summary="s", mechanism="m", category="other"),
            reasoning_chain=(),
            fix_strategy=FixStrategy(
                approach="a", files_to_modify=("services/quote.py",), regression_test_needed=False
            ),
            self_assessed_confidence=0.8,
            model="m",
            prompt_version="reason/v3",
        ),
        patch=Patch(
            patch_id="pat_1",
            base_commit="c1",
            diff="--- a/services/quote.py\n+++ b/services/quote.py\n@@ -1 +1 @@\n-a\n+b\n",
            files_changed=(),
            explanation="Removed the None fallback.",
            risk_assessment=RiskAssessment(level="low"),
            model="m",
            prompt_version="patch/v4",
        ),
        failed_gate=failed_gate,
        gate_specific_instruction=(
            "Your patch broke tests that previously passed. Each is listed with its "
            "failure. Either preserve the existing contract, or update those tests and "
            "justify why the behaviour change is correct."
        ),
        failure_detail={"newly_failing": ["tests/test_quote.py::test_estimate_with_missing_tax"]},
        previous_attempts=previous_attempts,
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


def _repairer(provider: FakeProvider) -> GatewayRepairer:
    gateway = LLMGateway(
        providers={"anthropic": provider},
        routing=parse_model_routing(_routing_doc()),
        prompts=_PROMPTS,
        storage=InMemoryObjectStore(),
        db=InMemoryLLMCallsRepository(),
        sleep=_noop_sleep,
    )
    return GatewayRepairer(gateway=gateway, prompts=_PROMPTS, project_id="proj_1")


async def test_a_clean_reply_produces_an_instruction_delta() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    reply = await _repairer(provider).repair(_request())
    assert reply["instruction_delta"] == (
        "Keep the typed exception, but update tests/test_quote.py to assert the new behaviour."
    )


async def test_the_call_uses_the_fast_tier() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    await _repairer(provider).repair(_request())
    assert len(provider.calls) == 1
    assert provider.calls[0].model == "claude-haiku-4-5"


async def test_the_gate_and_instruction_are_injected_into_the_task_layer() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    await _repairer(provider).repair(_request(failed_gate="G6"))
    assert "FAILED GATE: G6" in provider.calls[0].system
    assert "Your patch broke tests that previously passed" in provider.calls[0].system


async def test_the_sandbox_failure_detail_and_diff_reach_the_prompt() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    await _repairer(provider).repair(_request())
    assert "test_estimate_with_missing_tax" in provider.calls[0].user
    assert "services/quote.py" in provider.calls[0].user


async def test_previous_attempts_reach_the_prompt_when_present() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    previous = (PreviousAttempt(attempt=1, failed_gate="G4", reason="theatrical test"),)
    await _repairer(provider).repair(_request(previous_attempts=previous))
    assert "theatrical test" in provider.calls[0].user


async def test_gateway_exhaustion_raises_repairer_unavailable() -> None:
    provider = FakeProvider(
        name="anthropic", outcomes=[ScriptedFailure(trigger="server_error", detail="down")]
    )
    with pytest.raises(RepairerUnavailable):
        await _repairer(provider).repair(_request())
