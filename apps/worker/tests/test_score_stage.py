"""`score()` (`03` §S11, T7.3) — `15`'s own accept criteria: 10
hand-computed scenarios, every hard gate individually verified,
`build_passed = false` produces `confidence = 0`. Every expected number
in this file is computed by hand in the docstring/comment above its
assertion, from `03` §S11's own formula — not copied from the
implementation, so a bug in `stage.py` has a real chance of being caught
here rather than both sides agreeing by construction."""

from __future__ import annotations

import pytest

from roottrace_worker.pipeline.critique.contracts import (
    Critique,
    Finding,
    SecurityReview,
    TestQuality,
)
from roottrace_worker.pipeline.reason.contracts import (
    Evidence,
    FixStrategy,
    ReasoningStep,
    RootCause,
    RootCauseAnalysis,
)
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
from roottrace_worker.pipeline.score.stage import (
    _apply_band_cap,
    _band,
    _validation_component,
    score,
)
from roottrace_worker.pipeline.understand.contracts import (
    ErrorUnderstanding,
    ExceptionFamily,
    ExceptionInfo,
    FailurePoint,
    RetrievalPlan,
)
from roottrace_worker.pipeline.validate.contracts import (
    ResourceUsage,
    SignalsForScoring,
    Transcript,
    ValidationResult,
)

pytestmark = pytest.mark.unit


def _validation(
    *,
    build_passed: bool = True,
    regression_test_valid: bool | None = True,
    test_pass_ratio: float | None = 1.0,
    new_high: int = 0,
    new_medium: int = 0,
    degraded_mode: bool = False,
    validation_component_cap: float | None = None,
    band_cap: str | None = None,
) -> ValidationResult:
    return ValidationResult(
        validation_id="val_1",
        passed=build_passed,
        mode="full",
        gates=(),
        failed_gate=None,
        resource_usage=ResourceUsage(
            wall_ms=100, cpu_ms=50, peak_memory_mb=10, peak_pids=1, disk_written_mb=0
        ),
        transcript=Transcript(stdout_bytes=0, stderr_bytes=0, truncated=False),
        signals_for_scoring=SignalsForScoring(
            build_passed=build_passed,
            regression_test_valid=regression_test_valid,
            test_pass_ratio=test_pass_ratio,
            new_static_findings_high=new_high,
            new_static_findings_medium=new_medium,
            degraded_mode=degraded_mode,
            validation_component_cap=validation_component_cap,
            band_cap=band_cap,  # type: ignore[arg-type]
        ),
    )


def _bundle(
    *, quality_score: float = 1.0, gaps: tuple[str, ...] = (), has_release_correlation: bool = False
) -> ContextBundle:
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
                content="def f():\n    return 1\n",
                line_range=(1, 2),
                truncated=False,
            ),
        ),
        graph=BundleGraph(),
        history=BundleHistory(),
        tests=BundleTests(),
        strategy_stats={},
        quality=Quality(
            score=quality_score,
            signals=QualitySignals(
                failure_point_resolved=True,
                entry_point_resolved=True,
                callees_resolved=0,
                callers_resolved=0,
                has_tests=False,
                has_release_correlation=has_release_correlation,
            ),
        ),
        gaps=gaps,
    )


def _understanding(
    *, failure_point_path: str | None = "services/checkout.py"
) -> ErrorUnderstanding:
    return ErrorUnderstanding(
        exception=ExceptionInfo(
            type="TaxServiceUnavailable",
            family=ExceptionFamily.INTEGRATION,
            message_normalized="tax service unavailable",
            is_user_facing=True,
        ),
        failure_point=FailurePoint(repo_path=failure_point_path) if failure_point_path else None,
        retrieval_plan=RetrievalPlan(),
        extraction_confidence=0.9,
    )


def _analysis(
    *,
    self_assessed_confidence: float = 0.9,
    category: str = "logic_error",
    cites_failure_point: bool = True,
    num_kept_steps: int = 1,
) -> RootCauseAnalysis:
    evidence = (
        (Evidence(kind="file", repo_path="services/checkout.py", line_range=(1, 2), excerpt="x"),)
        if cites_failure_point
        else ()
    )
    steps = tuple(
        ReasoningStep(
            step=i + 1, type="conclude", statement="s", evidence=evidence if i == 0 else ()
        )
        for i in range(num_kept_steps)
    )
    return RootCauseAnalysis(
        root_cause=RootCause(summary="s", mechanism="m", category=category),  # type: ignore[arg-type]
        reasoning_chain=steps,
        fix_strategy=FixStrategy(approach="a", regression_test_needed=False),
        self_assessed_confidence=self_assessed_confidence,
        model="m",
        prompt_version="reason/v3",
    )


def _critique(
    *,
    verdict: str = "approve",
    agreement_with_diagnosis: float = 1.0,
    findings: tuple[Finding, ...] = (),
) -> Critique:
    return Critique(
        critique_id="crit_1",
        verdict=verdict,  # type: ignore[arg-type]
        agreement_with_diagnosis=agreement_with_diagnosis,
        addresses_reported_error=True,
        findings=findings,
        security_review=SecurityReview(concerns=(), clean=True),
        regression_risk="low",
        test_quality=TestQuality(reproduces_bug=True, assessment="genuine"),
        scope_assessment="tight",
        blocking=False,
        model="m",
        prompt_version="critique/v2",
    )


# ---------------------------------------------------------------------------
# 10 hand-computed scenarios (15's own accept criterion)
# ---------------------------------------------------------------------------


def test_scenario_1_everything_clean_is_high_confidence() -> None:
    """validation=0.30+0.25+0.25+0.10+0.10=1.00; critic=1.00*1.00=1.00;
    retrieval=1.00; evidence=0.50+0.20+0.15+0.15=1.00; self=0.90;
    historical=0.50.
    confidence = .30*1+.20*1+.15*1+.15*1+.10*.9+.10*.5
               = .30+.20+.15+.15+.09+.05 = 0.94 -> high."""
    result = score(
        validation=_validation(),
        attempts=1,
        critique=_critique(),
        bundle=_bundle(quality_score=1.0, has_release_correlation=True),
        understanding=_understanding(),
        analysis=_analysis(),
    )
    assert result.confidence == pytest.approx(0.94)
    assert result.band == "high"
    assert result.gates_applied == ()
    assert result.should_publish is True
    assert result.publish_mode == "open_pr"


def test_scenario_2_build_failed_produces_confidence_zero() -> None:
    """15's own literal accept criterion: build_passed = false ->
    confidence = 0, regardless of every other component."""
    result = score(
        validation=_validation(build_passed=False),
        attempts=1,
        critique=_critique(),
        bundle=_bundle(quality_score=1.0),
        understanding=_understanding(),
        analysis=_analysis(),
    )
    assert result.confidence == 0.0
    assert result.gates_applied == ("build_passed_false",)
    assert result.band == "insufficient"
    assert result.should_publish is False
    assert result.publish_mode == "analysis_only"


def test_scenario_3_critic_reject_caps_at_0_25() -> None:
    """validation=1.00 (as scenario 1); critic verdict=reject -> 0.00
    regardless of agreement; retrieval=1.00; evidence=1.00; self=.90;
    historical=.50.
    raw confidence = .30*1+.20*0+.15*1+.15*1+.10*.9+.10*.5
                    = .30+0+.15+.15+.09+.05 = 0.74
    hard gate: critic_reject -> min(0.74, 0.25) = 0.25 -> insufficient."""
    result = score(
        validation=_validation(),
        attempts=1,
        critique=_critique(verdict="reject", agreement_with_diagnosis=0.9),
        bundle=_bundle(quality_score=1.0, has_release_correlation=True),
        understanding=_understanding(),
        analysis=_analysis(),
    )
    assert result.confidence == pytest.approx(0.25)
    assert result.gates_applied == ("critic_reject",)
    assert result.band == "insufficient"
    assert result.should_publish is False


def test_scenario_4_critical_security_finding_zeroes_confidence() -> None:
    """Everything else strong (as scenario 1, critic verdict
    approve_with_notes: critic=.80*1.0=.80), but a critical/security
    finding is present -> confidence forced to 0 regardless of the
    arithmetic that would otherwise have produced roughly
    .30+.16+.15+.15+.09+.05=0.90."""
    result = score(
        validation=_validation(),
        attempts=1,
        critique=_critique(
            verdict="approve_with_notes",
            findings=(
                Finding(
                    severity="critical",
                    dimension="security",
                    statement="hardcoded backdoor",
                ),
            ),
        ),
        bundle=_bundle(quality_score=1.0, has_release_correlation=True),
        understanding=_understanding(),
        analysis=_analysis(),
    )
    assert result.confidence == 0.0
    assert result.gates_applied == ("critical_security_finding",)
    assert result.band == "insufficient"
    assert result.should_publish is False


def test_scenario_5_regression_test_invalid_caps_at_0_50() -> None:
    """validation = .30+0(regression false)+.25+.10+.10 = 0.75; critic=1.00;
    retrieval=1.00; evidence=1.00; self=.90; historical=.50.
    raw = .30*.75+.20*1+.15*1+.15*1+.10*.9+.10*.5
        = .225+.20+.15+.15+.09+.05 = 0.865
    hard gate: regression_test_invalid -> min(0.865, 0.50) = 0.50 -> low
    (this gate does not force should_publish=False)."""
    result = score(
        validation=_validation(regression_test_valid=False),
        attempts=1,
        critique=_critique(),
        bundle=_bundle(quality_score=1.0, has_release_correlation=True),
        understanding=_understanding(),
        analysis=_analysis(),
    )
    assert result.confidence == pytest.approx(0.50)
    assert result.gates_applied == ("regression_test_invalid",)
    assert result.band == "low"
    assert result.should_publish is True
    assert result.publish_mode == "open_draft_pr"


def test_scenario_6_low_retrieval_quality_caps_at_0_45() -> None:
    """retrieval = 0.30 (quality.score, no gaps); everything else as
    scenario 1 (validation=1.00, critic=1.00, evidence=1.00, self=.90,
    historical=.50).
    raw = .30*1+.20*1+.15*.30+.15*1+.10*.9+.10*.5
        = .30+.20+.045+.15+.09+.05 = 0.835
    hard gate: retrieval_quality_low (0.30 < 0.4) -> min(0.835, 0.45)
             = 0.45 -> low."""
    result = score(
        validation=_validation(),
        attempts=1,
        critique=_critique(),
        bundle=_bundle(quality_score=0.30, has_release_correlation=True),
        understanding=_understanding(),
        analysis=_analysis(),
    )
    assert result.confidence == pytest.approx(0.45)
    assert result.gates_applied == ("retrieval_quality_low",)
    assert result.band == "low"
    assert result.should_publish is True


def test_scenario_7_medium_confidence_with_notes_and_a_repair_attempt() -> None:
    """validation = .30+.25+.9*.25+.10+.05(medium findings exist)
                   -.05*(2-1) = .30+.25+.225+.10+.05-.05 = 0.875
    critic = .80*.9 - .05*1(medium) = .72-.05 = 0.67
    retrieval = .70-.05*1(one gap) = 0.65
    evidence = .50(1 kept/1 total)+0(no failure-point citation)
              +0(no release correlation)+.15(not external) = 0.65
    self = .75; historical=.50.
    raw = .30*.875+.20*.67+.15*.65+.15*.65+.10*.75+.10*.5
        = .2625+.134+.0975+.0975+.075+.05 = 0.7165 -> medium."""
    result = score(
        validation=_validation(test_pass_ratio=0.9, new_medium=1),
        attempts=2,
        critique=_critique(
            verdict="approve_with_notes",
            agreement_with_diagnosis=0.9,
            findings=(Finding(severity="medium", dimension="style", statement="nit"),),
        ),
        bundle=_bundle(quality_score=0.70, gaps=("get_regional_config",)),
        understanding=_understanding(failure_point_path=None),
        analysis=_analysis(
            self_assessed_confidence=0.75, cites_failure_point=False, num_kept_steps=1
        ),
    )
    assert result.confidence == pytest.approx(0.7165)
    assert result.band == "medium"


def test_scenario_8_request_changes_with_a_dropped_claim_is_low() -> None:
    """validation=1.00 (as scenario 1). critic = .35*.6-.15(1 high)
      -.05(1 medium) = .21-.15-.05 = 0.01
    retrieval = 0.90 (no gaps). evidence = .5*(1 kept/2 total)
      +0(no citation)+0(no release)+.15(not external) = .25+.15=0.40
    self=.50; historical=.50.
    raw = .30*1+.20*.01+.15*.9+.15*.40+.10*.5+.10*.5
        = .30+.002+.135+.06+.05+.05 = 0.597 -> low (just under .60)."""
    result = score(
        validation=_validation(),
        attempts=1,
        critique=_critique(
            verdict="request_changes",
            agreement_with_diagnosis=0.6,
            findings=(
                Finding(severity="high", dimension="correctness", statement="x"),
                Finding(severity="medium", dimension="style", statement="y"),
            ),
        ),
        bundle=_bundle(quality_score=0.90),
        understanding=_understanding(failure_point_path=None),
        analysis=_analysis(
            self_assessed_confidence=0.5,
            category="other",
            cites_failure_point=False,
            num_kept_steps=1,
        ),
        dropped_claims=("a fabricated citation was dropped",),
    )
    assert result.confidence == pytest.approx(0.597)
    assert result.band == "low"


def test_scenario_9_no_critique_available_scores_critic_component_zero() -> None:
    """03 S10: "proceed to S11 with critic_component = 0." validation=1.00
    (as scenario 1); critic=0.00; retrieval=1.00; evidence=1.00; self=.90;
    historical=.50.
    raw = .30*1+.20*0+.15*1+.15*1+.10*.9+.10*.5
        = .30+0+.15+.15+.09+.05 = 0.74 -> medium."""
    result = score(
        validation=_validation(),
        attempts=1,
        critique=None,
        bundle=_bundle(quality_score=1.0, has_release_correlation=True),
        understanding=_understanding(),
        analysis=_analysis(),
    )
    assert result.confidence == pytest.approx(0.74)
    assert result.band == "medium"
    assert "unavailable" in result.explanation.lower()


def test_scenario_10_external_dependency_loses_the_actionable_cause_bonus() -> None:
    """validation=1.00; critic=1.00; retrieval=1.00; evidence =
    .50(1/1 kept)+.20(cites failure point)+.15(release correlation)
    +0(category IS external_dependency, no actionable-cause bonus) = 0.85
    self=.90; historical=.50.
    raw = .30*1+.20*1+.15*1+.15*.85+.10*.9+.10*.5
        = .30+.20+.15+.1275+.09+.05 = 0.9175 -> high."""
    result = score(
        validation=_validation(),
        attempts=1,
        critique=_critique(),
        bundle=_bundle(quality_score=1.0, has_release_correlation=True),
        understanding=_understanding(),
        analysis=_analysis(category="external_dependency"),
    )
    assert result.confidence == pytest.approx(0.9175)
    assert result.band == "high"


# ---------------------------------------------------------------------------
# Band boundaries and cap mechanisms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("confidence", "expected"),
    [
        (0.80, "high"),
        (0.79, "medium"),
        (0.60, "medium"),
        (0.59, "low"),
        (0.40, "low"),
        (0.39, "insufficient"),
    ],
)
def test_band_boundaries_are_inclusive_on_the_low_end(confidence: float, expected: str) -> None:
    assert _band(confidence) == expected


def test_validation_component_cap_binds_when_the_raw_value_exceeds_it() -> None:
    """Isolated proof the cap mechanism itself works — 07 S5's actual
    partial/syntax_only modes never produce a raw value the cap would
    bind against in practice (see PROJECT-STATUS.md's T7.3 section), so
    this constructs a case the cap DOES change regardless of realism."""
    signals = SignalsForScoring(
        build_passed=True,
        regression_test_valid=True,
        test_pass_ratio=1.0,
        validation_component_cap=0.55,
    )
    assert _validation_component(signals, attempts=1) == pytest.approx(0.55)


def test_validation_component_cap_does_not_bind_when_the_raw_value_is_already_lower() -> None:
    """raw = 0(build failed)+0(regression None)+0(ratio None)+.10(no new
    high)+.10(no new medium) = 0.20 -- the "no new findings" terms are
    unconditional, not gated on `build_passed` -- well under the 0.55 cap."""
    signals = SignalsForScoring(
        build_passed=False, regression_test_valid=None, validation_component_cap=0.55
    )
    assert _validation_component(signals, attempts=1) == pytest.approx(0.20)


def test_band_cap_downgrades_a_higher_band() -> None:
    assert _apply_band_cap("high", "low") == "low"
    assert _apply_band_cap("medium", "low") == "low"


def test_band_cap_never_upgrades() -> None:
    assert _apply_band_cap("insufficient", "low") == "insufficient"


def test_band_cap_of_none_is_a_no_op() -> None:
    assert _apply_band_cap("high", None) == "high"


def test_explanation_names_degraded_mode_when_regression_test_valid_is_none() -> None:
    result = score(
        validation=_validation(regression_test_valid=None, test_pass_ratio=None),
        attempts=1,
        critique=_critique(),
        bundle=_bundle(quality_score=1.0),
        understanding=_understanding(),
        analysis=_analysis(),
    )
    assert "degraded sandbox mode" in result.explanation


def test_partial_mode_degraded_signals_are_already_below_the_cap_without_it() -> None:
    """A real T6.5 `partial`-mode result skips G4 and G6 together
    (regression_test_valid and test_pass_ratio both None), so the raw
    validation component tops out at .30(build)+.10(no high)+.10(no
    medium) = 0.50 -- already under the 0.55 cap `07` S5 names. Confirms
    the observation disclosed in PROJECT-STATUS.md: the cap is a real,
    correctly-implemented safety net, but T6.5's own conservative
    None-handling means it does not change the outcome in the one real
    scenario that produces it."""
    signals = SignalsForScoring(
        build_passed=True,
        regression_test_valid=None,
        test_pass_ratio=None,
        validation_component_cap=0.55,
    )
    assert _validation_component(signals, attempts=1) == pytest.approx(0.50)


def test_syntax_only_mode_build_passed_false_already_zeroes_confidence_without_the_band_cap() -> (
    None
):
    """A real T6.5 `syntax_only`-mode result reports `build_passed:
    False` (T6.5's own stage.py decision), which S11's own hard gate
    already zeroes to `confidence = 0` / band `insufficient` -- below
    what `band_cap: "low"` would even downgrade to. Same observation as
    the partial-mode test above, for the other degraded mode."""
    result = score(
        validation=_validation(build_passed=False, band_cap="low"),
        attempts=1,
        critique=_critique(),
        bundle=_bundle(quality_score=1.0),
        understanding=_understanding(),
        analysis=_analysis(),
    )
    assert result.confidence == 0.0
    assert result.band == "insufficient"


# ---------------------------------------------------------------------------
# auto_merge_eligible / should_publish / publish_mode
# ---------------------------------------------------------------------------


def _high_band_inputs() -> tuple[
    ValidationResult, Critique, ContextBundle, ErrorUnderstanding, RootCauseAnalysis
]:
    return (
        _validation(),
        _critique(),
        _bundle(quality_score=1.0, has_release_correlation=True),
        _understanding(),
        _analysis(),
    )


def test_auto_merge_requires_high_band_and_both_caller_supplied_flags() -> None:
    validation, critique, bundle, understanding, analysis = _high_band_inputs()
    assert (
        score(
            validation=validation,
            attempts=1,
            critique=critique,
            bundle=bundle,
            understanding=understanding,
            analysis=analysis,
        ).auto_merge_eligible
        is False
    )  # defaults: opt_in=False, path_matches=False

    assert (
        score(
            validation=validation,
            attempts=1,
            critique=critique,
            bundle=bundle,
            understanding=understanding,
            analysis=analysis,
            repo_opt_in=True,
        ).auto_merge_eligible
        is False
    )  # path_matches still False

    assert (
        score(
            validation=validation,
            attempts=1,
            critique=critique,
            bundle=bundle,
            understanding=understanding,
            analysis=analysis,
            path_matches=True,
        ).auto_merge_eligible
        is False
    )  # repo_opt_in still False

    assert (
        score(
            validation=validation,
            attempts=1,
            critique=critique,
            bundle=bundle,
            understanding=understanding,
            analysis=analysis,
            repo_opt_in=True,
            path_matches=True,
        ).auto_merge_eligible
        is True
    )


def test_auto_merge_is_never_eligible_below_high_band() -> None:
    result = score(
        validation=_validation(test_pass_ratio=0.5),
        attempts=1,
        critique=_critique(verdict="approve_with_notes", agreement_with_diagnosis=0.7),
        bundle=_bundle(quality_score=0.6),
        understanding=_understanding(failure_point_path=None),
        analysis=_analysis(cites_failure_point=False),
        repo_opt_in=True,
        path_matches=True,
    )
    assert result.band != "high"
    assert result.auto_merge_eligible is False
