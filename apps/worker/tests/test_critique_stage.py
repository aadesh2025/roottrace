"""`critique()` (`03` §S10, T7.2) — the stage entrypoint, tested against
hand-built `StructuredCritic` doubles rather than `GatewayCritic`, same
pattern `test_patch_stage.py` establishes for S7."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from roottrace_worker.pipeline.critique.critic import CriticUnavailable, CritiqueRequest
from roottrace_worker.pipeline.critique.stage import CritiqueOutcome, critique
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


def _bundle() -> ContextBundle:
    return ContextBundle(
        bundle_id="ctx_1",
        repository=RepositoryRef(full_name="acme/checkout-api", ref="main"),
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
        diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
        validation=_validation(),
    )


def _reply(**overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "verdict": "approve",
        "agreement_with_diagnosis": 0.95,
        "addresses_reported_error": True,
        "findings": [],
        "security_review": {"concerns": [], "clean": True},
        "regression_risk": "low",
        "test_quality": {"reproduces_bug": True, "assessment": "Genuine."},
        "scope_assessment": "Tightly scoped.",
        "blocking": False,
        "model": "claude-sonnet-5",
        "prompt_version": "critique/v2",
        "tokens": {"prompt": 500, "completion": 80},
    }
    payload.update(overrides)
    return payload


class _RaisingCritic:
    async def critique(self, request: CritiqueRequest) -> Mapping[str, Any]:
        raise CriticUnavailable("no provider available")


class _NonMappingCritic:
    async def critique(self, request: CritiqueRequest) -> Any:
        return "not a mapping"


class _MalformedCritic:
    async def critique(self, request: CritiqueRequest) -> Mapping[str, Any]:
        return {"verdict": "not-a-real-verdict"}


class _WellBehavedCritic:
    async def critique(self, request: CritiqueRequest) -> Mapping[str, Any]:
        return _reply()


class _RejectingCritic:
    async def critique(self, request: CritiqueRequest) -> Mapping[str, Any]:
        return _reply(
            verdict="reject",
            blocking=False,  # the model's own self-report -- must not be trusted
            addresses_reported_error=False,
            scope_assessment="Introduces an unrelated auth bypass.",
        )


class _CriticalFindingCritic:
    async def critique(self, request: CritiqueRequest) -> Mapping[str, Any]:
        return _reply(
            verdict="approve_with_notes",
            blocking=False,  # the model's own self-report -- must not be trusted
            findings=[
                {
                    "severity": "critical",
                    "dimension": "security",
                    "statement": "Hardcoded admin bypass introduced.",
                    "recommendation": "Remove it.",
                }
            ],
        )


class _InconsistentSecurityReviewCritic:
    async def critique(self, request: CritiqueRequest) -> Mapping[str, Any]:
        return _reply(security_review={"concerns": ["disabled TLS verification"], "clean": True})


async def test_critic_unavailable_becomes_an_honest_unavailable_outcome() -> None:
    outcome = await critique(_request(), critic=_RaisingCritic(), critique_id="crit_1")
    assert outcome.critique is None
    assert outcome.unavailable is not None
    assert "no provider available" in outcome.unavailable.reason


async def test_a_non_mapping_reply_becomes_unavailable_not_a_crash() -> None:
    outcome = await critique(_request(), critic=_NonMappingCritic(), critique_id="crit_1")
    assert outcome.unavailable is not None
    assert "non-mapping" in outcome.unavailable.reason


async def test_a_reply_that_fails_schema_validation_becomes_unavailable() -> None:
    outcome = await critique(_request(), critic=_MalformedCritic(), critique_id="crit_1")
    assert outcome.unavailable is not None
    assert "schema validation" in outcome.unavailable.reason


async def test_a_well_formed_reply_produces_a_critique_with_the_caller_supplied_id() -> None:
    outcome = await critique(_request(), critic=_WellBehavedCritic(), critique_id="crit_42")
    assert outcome.critique is not None
    assert outcome.critique.critique_id == "crit_42"
    assert outcome.critique.verdict == "approve"
    assert outcome.critique.model == "claude-sonnet-5"
    assert outcome.critique.tokens.prompt == 500


async def test_a_reject_verdict_is_blocking_regardless_of_the_models_own_flag() -> None:
    outcome = await critique(_request(), critic=_RejectingCritic(), critique_id="crit_1")
    assert outcome.critique is not None
    assert outcome.critique.verdict == "reject"
    assert outcome.critique.blocking is True


async def test_a_critical_finding_is_blocking_even_with_a_non_reject_verdict() -> None:
    outcome = await critique(_request(), critic=_CriticalFindingCritic(), critique_id="crit_1")
    assert outcome.critique is not None
    assert outcome.critique.verdict == "approve_with_notes"
    assert outcome.critique.blocking is True


async def test_an_approve_verdict_with_no_findings_is_not_blocking() -> None:
    outcome = await critique(_request(), critic=_WellBehavedCritic(), critique_id="crit_1")
    assert outcome.critique is not None
    assert outcome.critique.blocking is False


async def test_security_review_clean_is_recomputed_not_trusted() -> None:
    """A model claiming `clean: true` while also listing a concern is a
    contradiction `stage.py` corrects rather than propagates."""
    outcome = await critique(
        _request(), critic=_InconsistentSecurityReviewCritic(), critique_id="crit_1"
    )
    assert outcome.critique is not None
    assert outcome.critique.security_review.clean is False
    assert outcome.critique.security_review.concerns == ("disabled TLS verification",)


def test_critique_outcome_requires_exactly_one_of_critique_or_unavailable() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        CritiqueOutcome()
