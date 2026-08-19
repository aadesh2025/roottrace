"""`repair()` (`03` §S9, T7.1) — the stage entrypoint, tested against
hand-built `StructuredRepairer` doubles rather than `GatewayRepairer`, so
the stage's own deterministic routing and exhaustion logic are exercised
directly, same pattern `test_patch_stage.py` establishes for S7."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from roottrace_worker.pipeline.patch.contracts import Patch, RiskAssessment
from roottrace_worker.pipeline.reason.contracts import FixStrategy, RootCause, RootCauseAnalysis
from roottrace_worker.pipeline.repair.contracts import PreviousAttempt
from roottrace_worker.pipeline.repair.repairer import RepairerUnavailable, RepairRequest
from roottrace_worker.pipeline.repair.routing import UnroutableGateError
from roottrace_worker.pipeline.repair.stage import RepairOutcome, repair
from roottrace_worker.pipeline.understand.contracts import (
    ErrorUnderstanding,
    ExceptionFamily,
    ExceptionInfo,
    RetrievalPlan,
)
from roottrace_worker.pipeline.validate.contracts import (
    GateResult,
    ResourceUsage,
    SignalsForScoring,
    Transcript,
    ValidationResult,
)

pytestmark = pytest.mark.unit


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


def _root_cause() -> RootCauseAnalysis:
    return RootCauseAnalysis(
        root_cause=RootCause(summary="s", mechanism="m", category="other"),
        reasoning_chain=(),
        fix_strategy=FixStrategy(
            approach="a", files_to_modify=("services/checkout.py",), regression_test_needed=False
        ),
        self_assessed_confidence=0.8,
        model="m",
        prompt_version="reason/v3",
    )


def _patch() -> Patch:
    return Patch(
        patch_id="pat_1",
        base_commit="c1",
        diff="--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n",
        files_changed=(),
        explanation="e",
        risk_assessment=RiskAssessment(level="low"),
        model="m",
        prompt_version="patch/v4",
    )


def _resource_usage() -> ResourceUsage:
    return ResourceUsage(wall_ms=100, cpu_ms=50, peak_memory_mb=10, peak_pids=1, disk_written_mb=0)


def _transcript() -> Transcript:
    return Transcript(stdout_bytes=0, stderr_bytes=0, truncated=False)


def _failed_validation(
    *, failed_gate: str, gates: tuple[GateResult, ...] | None = None
) -> ValidationResult:
    if gates is None:
        gates = (
            GateResult(
                gate=failed_gate,
                passed=False,
                duration_ms=10,
                detail={"reason": f"{failed_gate} failed for a documented reason"},
            ),
        )
    return ValidationResult(
        validation_id="val_1",
        passed=False,
        mode="full",
        gates=gates,
        failed_gate=failed_gate,
        resource_usage=_resource_usage(),
        transcript=_transcript(),
        signals_for_scoring=SignalsForScoring(build_passed=True, regression_test_valid=False),
    )


class _RaisingRepairer:
    async def repair(self, request: RepairRequest) -> Mapping[str, Any]:
        raise RepairerUnavailable("no provider available")


class _WellBehavedRepairer:
    async def repair(self, request: RepairRequest) -> Mapping[str, Any]:
        return {"instruction_delta": f"model-authored guidance for {request.failed_gate}"}


class _EmptyReplyRepairer:
    async def repair(self, request: RepairRequest) -> Mapping[str, Any]:
        return {"instruction_delta": "   "}


async def test_a_passed_validation_is_rejected() -> None:
    passed = ValidationResult(
        validation_id="val_1",
        passed=True,
        mode="full",
        gates=(),
        failed_gate=None,
        resource_usage=_resource_usage(),
        transcript=_transcript(),
        signals_for_scoring=SignalsForScoring(build_passed=True, regression_test_valid=True),
    )
    with pytest.raises(ValueError, match="only for a failed"):
        await repair(
            passed,
            understanding=_understanding(),
            root_cause=_root_cause(),
            patch=_patch(),
            previous_attempts=(),
            attempt=1,
            repair_id="rep_1",
        )


async def test_an_unroutable_gate_propagates() -> None:
    with pytest.raises(UnroutableGateError):
        await repair(
            _failed_validation(failed_gate="timeout", gates=()),
            understanding=_understanding(),
            root_cause=_root_cause(),
            patch=_patch(),
            previous_attempts=(),
            attempt=1,
            repair_id="rep_1",
        )


async def test_attempt_exhaustion_terminates_before_routing_even_for_an_unroutable_gate() -> None:
    """`03` §S9's algorithm checks `attempt >= max_attempts` first — an
    already-exhausted repair loop must terminate cleanly even if the final
    gate happens to be one `routing.py` cannot route, since no routing
    decision is ever needed for a terminal outcome."""
    outcome = await repair(
        _failed_validation(failed_gate="timeout", gates=()),
        understanding=_understanding(),
        root_cause=_root_cause(),
        patch=_patch(),
        previous_attempts=(
            PreviousAttempt(attempt=1, failed_gate="G6", reason="broke a test"),
            PreviousAttempt(attempt=2, failed_gate="G5", reason="fix did not fix it"),
        ),
        attempt=3,
        repair_id="rep_1",
        max_attempts=3,
    )
    assert outcome.exhausted is not None
    assert outcome.decision is None
    assert outcome.exhausted.attempt == 3
    assert outcome.exhausted.failed_gate == "timeout"
    assert len(outcome.exhausted.attempts_summary) == 3


async def test_g5_reroutes_to_reasoning_not_patching() -> None:
    outcome = await repair(
        _failed_validation(failed_gate="G5"),
        understanding=_understanding(),
        root_cause=_root_cause(),
        patch=_patch(),
        previous_attempts=(),
        attempt=1,
        repair_id="rep_1",
    )
    assert outcome.decision is not None
    assert outcome.decision.strategy == "reconsider_root_cause"
    assert outcome.decision.reroute_to_stage == "S6"


async def test_every_other_gate_reroutes_to_patching() -> None:
    outcome = await repair(
        _failed_validation(failed_gate="G6"),
        understanding=_understanding(),
        root_cause=_root_cause(),
        patch=_patch(),
        previous_attempts=(),
        attempt=1,
        repair_id="rep_1",
    )
    assert outcome.decision is not None
    assert outcome.decision.reroute_to_stage == "S7"


async def test_with_no_repairer_the_deterministic_instruction_is_used_directly() -> None:
    outcome = await repair(
        _failed_validation(failed_gate="G1"),
        understanding=_understanding(),
        root_cause=_root_cause(),
        patch=_patch(),
        previous_attempts=(),
        attempt=1,
        repair_id="rep_1",
        repairer=None,
    )
    assert outcome.decision is not None
    assert "Fix only the syntax error" in outcome.decision.instruction_delta


async def test_a_repairer_reply_overrides_the_deterministic_instruction() -> None:
    outcome = await repair(
        _failed_validation(failed_gate="G1"),
        understanding=_understanding(),
        root_cause=_root_cause(),
        patch=_patch(),
        previous_attempts=(),
        attempt=1,
        repair_id="rep_1",
        repairer=_WellBehavedRepairer(),
    )
    assert outcome.decision is not None
    assert outcome.decision.instruction_delta == "model-authored guidance for G1"


async def test_repairer_unavailable_falls_back_to_the_deterministic_instruction_not_terminal() -> (
    None
):
    """`03` §S4's precedent for a fast-tier enhancement over a real floor:
    the model failing degrades quality, never terminates the stage."""
    outcome = await repair(
        _failed_validation(failed_gate="G2"),
        understanding=_understanding(),
        root_cause=_root_cause(),
        patch=_patch(),
        previous_attempts=(),
        attempt=1,
        repair_id="rep_1",
        repairer=_RaisingRepairer(),
    )
    assert outcome.decision is not None
    assert "Your patch imports a package unavailable" in outcome.decision.instruction_delta


async def test_a_blank_repairer_reply_falls_back_to_the_deterministic_instruction() -> None:
    outcome = await repair(
        _failed_validation(failed_gate="G3"),
        understanding=_understanding(),
        root_cause=_root_cause(),
        patch=_patch(),
        previous_attempts=(),
        attempt=1,
        repair_id="rep_1",
        repairer=_EmptyReplyRepairer(),
    )
    assert outcome.decision is not None
    assert "compiler output is verbatim" in outcome.decision.instruction_delta


async def test_previous_attempts_accumulate_in_order() -> None:
    outcome = await repair(
        _failed_validation(failed_gate="G6"),
        understanding=_understanding(),
        root_cause=_root_cause(),
        patch=_patch(),
        previous_attempts=(PreviousAttempt(attempt=1, failed_gate="G4", reason="theatrical test"),),
        attempt=2,
        repair_id="rep_1",
    )
    assert outcome.decision is not None
    summary = outcome.decision.previous_attempts_summary
    assert [a.attempt for a in summary] == [1, 2]
    assert summary[1].failed_gate == "G6"


async def test_a_routable_gate_with_no_matching_gate_entry_still_gets_a_decision() -> None:
    """A `G1`-`G8` `failed_gate` with no matching entry in `validation.
    gates` is not something T6.4's own fail-fast design should ever
    produce, but `stage.py` degrades gracefully rather than raising —
    `_failure_reason` falls back to naming the raw gate, and the
    deterministic instruction still stands."""
    outcome = await repair(
        _failed_validation(failed_gate="G6", gates=()),
        understanding=_understanding(),
        root_cause=_root_cause(),
        patch=_patch(),
        previous_attempts=(),
        attempt=1,
        repair_id="rep_1",
    )
    assert outcome.decision is not None
    assert "no gate detail available" in outcome.decision.previous_attempts_summary[0].reason


async def test_g6_derives_a_reason_from_newly_failing_when_no_reason_key_is_present() -> None:
    gates = (
        GateResult(
            gate="G6",
            passed=False,
            duration_ms=10,
            detail={"newly_failing": ["tests/test_x.py::test_y"], "already_failing": []},
        ),
    )
    outcome = await repair(
        _failed_validation(failed_gate="G6", gates=gates),
        understanding=_understanding(),
        root_cause=_root_cause(),
        patch=_patch(),
        previous_attempts=(),
        attempt=1,
        repair_id="rep_1",
    )
    assert outcome.decision is not None
    reason = outcome.decision.previous_attempts_summary[0].reason
    assert "tests/test_x.py::test_y" in reason


def test_repair_outcome_requires_exactly_one_of_decision_or_exhausted() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        RepairOutcome()


async def test_a_failed_gate_of_none_is_rejected() -> None:
    invalid = ValidationResult(
        validation_id="val_1",
        passed=False,
        mode="full",
        gates=(),
        failed_gate=None,
        resource_usage=_resource_usage(),
        transcript=_transcript(),
        signals_for_scoring=SignalsForScoring(build_passed=False, regression_test_valid=None),
    )
    with pytest.raises(ValueError, match="failed_gate is required"):
        await repair(
            invalid,
            understanding=_understanding(),
            root_cause=_root_cause(),
            patch=_patch(),
            previous_attempts=(),
            attempt=1,
            repair_id="rep_1",
        )


async def test_g8_derives_a_reason_from_findings_when_no_reason_key_is_present() -> None:
    gates = (
        GateResult(
            gate="G8",
            passed=False,
            duration_ms=10,
            detail={"findings": [{"path": "x.py", "severity": "high", "pattern": r"\beval\("}]},
        ),
    )
    outcome = await repair(
        _failed_validation(failed_gate="G8", gates=gates),
        understanding=_understanding(),
        root_cause=_root_cause(),
        patch=_patch(),
        previous_attempts=(),
        attempt=1,
        repair_id="rep_1",
    )
    assert outcome.decision is not None
    reason = outcome.decision.previous_attempts_summary[0].reason
    assert "1 dangerous construct" in reason
