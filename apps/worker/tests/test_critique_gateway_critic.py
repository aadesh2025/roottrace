"""`GatewayCritic` (T7.2) — the real `StructuredCritic`, exercised
end-to-end through `critique(...)` with a `FakeProvider` standing in for
the model. Mirrors `test_patch_gateway_patcher.py`'s shape — `reasoning-b`
tier, one call, no bespoke correction-retry ladder."""

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
from roottrace_worker.pipeline.critique.critic import CriticUnavailable, CritiqueRequest
from roottrace_worker.pipeline.critique.gateway_critic import GatewayCritic, split_critique_prompt
from roottrace_worker.pipeline.critique.stage import critique
from roottrace_worker.pipeline.retrieve.bundle import (
    BundleFile,
    BundleGraph,
    BundleHistory,
    BundleTests,
    ContextBundle,
    Quality,
    QualitySignals,
    RepositoryRef,
)
from roottrace_worker.pipeline.understand.contracts import (
    ErrorUnderstanding,
    ExceptionFamily,
    ExceptionInfo,
    RetrievalPlan,
)
from roottrace_worker.pipeline.validate.contracts import (
    ResourceUsage,
    SignalsForScoring,
    Transcript,
    ValidationResult,
)

pytestmark = pytest.mark.unit

_PROMPTS = load_prompt_registry()

VALID_REPLY = json.dumps(
    {
        "verdict": "approve_with_notes",
        "agreement_with_diagnosis": 0.9,
        "addresses_reported_error": True,
        "findings": [
            {
                "severity": "low",
                "dimension": "style",
                "statement": "Minor naming nit.",
                "recommendation": "Rename for consistency.",
            }
        ],
        "security_review": {"concerns": [], "clean": True},
        "regression_risk": "low",
        "test_quality": {"reproduces_bug": True, "assessment": "Genuine reproduction."},
        "scope_assessment": "Tightly scoped.",
        "blocking": False,
    }
)


def _understanding() -> ErrorUnderstanding:
    return ErrorUnderstanding(
        exception=ExceptionInfo(
            type="TaxServiceUnavailable",
            family=ExceptionFamily.INTEGRATION,
            message_normalized="tax service unavailable",
            is_user_facing=True,
        ),
        retrieval_plan=RetrievalPlan(),
        extraction_confidence=0.9,
    )


def _bundle() -> ContextBundle:
    return ContextBundle(
        bundle_id="ctx_1",
        repository=RepositoryRef(full_name="acme/checkout-api", ref="main", commit_sha="deadbeef"),
        token_count=100,
        token_budget=24_000,
        files=(
            BundleFile(
                repo_path="services/checkout.py",
                strategy="frame_direct",
                relevance=1.0,
                language="python",
                content="def calculate_total():\n    return 1\n",
                line_range=(1, 2),
                truncated=False,
            ),
        ),
        graph=BundleGraph(),
        history=BundleHistory(),
        tests=BundleTests(),
        strategy_stats={},
        quality=Quality(
            score=0.5,
            signals=QualitySignals(
                failure_point_resolved=True,
                entry_point_resolved=True,
                callees_resolved=0,
                callers_resolved=0,
                has_tests=False,
                has_release_correlation=False,
            ),
        ),
    )


def _validation() -> ValidationResult:
    return ValidationResult(
        validation_id="val_1",
        passed=True,
        mode="full",
        gates=(),
        failed_gate=None,
        resource_usage=ResourceUsage(
            wall_ms=100, cpu_ms=50, peak_memory_mb=10, peak_pids=1, disk_written_mb=0
        ),
        transcript=Transcript(stdout_bytes=0, stderr_bytes=0, truncated=False),
        signals_for_scoring=SignalsForScoring(build_passed=True, regression_test_valid=True),
    )


def _request() -> CritiqueRequest:
    return CritiqueRequest(
        understanding=_understanding(),
        bundle=_bundle(),
        diff="--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -1 +1 @@\n-a\n+b\n",
        validation=_validation(),
    )


def _routing_doc() -> dict[str, object]:
    return {
        "tiers": {
            "fast": [{"provider": "anthropic", "model": "claude-haiku-4-5"}],
            "reasoning-a": [{"provider": "anthropic", "model": "claude-sonnet-5"}],
            "reasoning-b": [{"provider": "openai", "model": "gpt-5"}],
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


def _critic(provider: FakeProvider) -> GatewayCritic:
    gateway = LLMGateway(
        providers={"openai": provider},
        routing=parse_model_routing(_routing_doc()),
        prompts=_PROMPTS,
        storage=InMemoryObjectStore(),
        db=InMemoryLLMCallsRepository(),
        sleep=_noop_sleep,
    )
    return GatewayCritic(gateway=gateway, prompts=_PROMPTS, project_id="proj_1")


def test_split_critique_prompt_separates_system_override_from_task() -> None:
    text = _PROMPTS.get("critique").text
    system_override, task = split_critique_prompt(text)
    assert "independent code review" in system_override
    assert "You did NOT write this patch" in system_override
    assert "CORRECTNESS" in task
    assert "<!--" not in system_override
    assert "<!--" not in task


def test_split_critique_prompt_raises_without_a_section_break() -> None:
    with pytest.raises(ValueError, match="section break"):
        split_critique_prompt("no break here at all")


async def test_a_clean_reply_produces_a_critique() -> None:
    provider = FakeProvider(name="openai", outcomes=[ScriptedSuccess(VALID_REPLY)])
    reply = await _critic(provider).critique(_request())
    assert reply["verdict"] == "approve_with_notes"
    assert reply["findings"][0]["dimension"] == "style"


async def test_the_call_uses_the_reasoning_b_tier() -> None:
    provider = FakeProvider(name="openai", outcomes=[ScriptedSuccess(VALID_REPLY)])
    await _critic(provider).critique(_request())
    assert len(provider.calls) == 1
    assert provider.calls[0].model == "gpt-5"


async def test_the_system_layer_is_the_override_not_the_shared_system_prompt() -> None:
    """`A2` §6: this stage's system layer *replaces* the shared L1
    entirely rather than extending it."""
    provider = FakeProvider(name="openai", outcomes=[ScriptedSuccess(VALID_REPLY)])
    await _critic(provider).critique(_request())
    assert "independent code review" in provider.calls[0].system
    assert "You did NOT write this patch" in provider.calls[0].system


async def test_the_diff_and_sandbox_results_reach_the_prompt_not_the_patch_explanation() -> None:
    provider = FakeProvider(name="openai", outcomes=[ScriptedSuccess(VALID_REPLY)])
    await _critic(provider).critique(_request())
    assert "services/checkout.py" in provider.calls[0].user
    assert '"passed": true' in provider.calls[0].user


async def test_a_deliberately_backdoored_patch_is_rejected() -> None:
    """`15` T7.2's own accept criterion. Backdoor detection is the
    model's job, not this stage's own (`03` §S10 is entirely LLM-driven —
    G8's static pattern scan already ran in the sandbox at S8) — this
    proves the *wiring*: a critic that genuinely catches a backdoor and
    returns `verdict: "reject"` propagates all the way through
    `critique()` into a `blocking: True` `Critique`, end to end through
    the real `GatewayCritic` and prompt assembly, not just the hand-built
    doubles `test_critique_stage.py` uses for the same property."""
    backdoored_diff = (
        "--- a/services/auth.py\n+++ b/services/auth.py\n@@ -1,2 +1,3 @@\n"
        " def check_permission(user, resource):\n"
        "+    if user.email == 'attacker@evil.example': return True\n"
        "     return user.has_role(resource.required_role)\n"
    )
    reject_reply = json.dumps(
        {
            "verdict": "reject",
            "agreement_with_diagnosis": 0.1,
            "addresses_reported_error": False,
            "findings": [
                {
                    "severity": "critical",
                    "dimension": "security",
                    "statement": "Hardcoded email check grants a specific attacker-controlled "
                    "account unconditional access, bypassing the role check entirely.",
                    "evidence": {"repo_path": "services/auth.py", "line_range": [2, 2]},
                    "recommendation": "Remove the backdoor; it has no legitimate purpose.",
                }
            ],
            "security_review": {
                "concerns": ["hardcoded authentication bypass for a specific account"],
                "clean": False,
            },
            "regression_risk": "high",
            "test_quality": {
                "reproduces_bug": False,
                "assessment": "The diff does not address the reported error at all.",
            },
            "scope_assessment": "Entirely unrelated to the reported error; introduces a "
            "new, unrequested authentication bypass.",
            "blocking": True,
        }
    )
    provider = FakeProvider(name="openai", outcomes=[ScriptedSuccess(reject_reply)])
    request = CritiqueRequest(
        understanding=_understanding(),
        bundle=_bundle(),
        diff=backdoored_diff,
        validation=_validation(),
    )

    outcome = await critique(request, critic=_critic(provider), critique_id="crit_backdoor")

    assert outcome.critique is not None
    assert outcome.critique.verdict == "reject"
    assert outcome.critique.blocking is True
    assert any(f.severity == "critical" for f in outcome.critique.findings)


async def test_gateway_exhaustion_raises_critic_unavailable() -> None:
    provider = FakeProvider(
        name="openai", outcomes=[ScriptedFailure(trigger="server_error", detail="down")]
    )
    with pytest.raises(CriticUnavailable):
        await _critic(provider).critique(_request())
