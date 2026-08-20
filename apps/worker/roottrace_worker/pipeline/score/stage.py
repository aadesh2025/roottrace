"""S11 `score` (`03` §S11) — T7.3.

**The one stage in this pipeline that is genuinely synchronous, not
`async def`.** `03` §S11 is explicit: "Pure computation, no LLM." Every
other stage built so far (`understand` through `critique`) is `async`
because it may call `LLMGateway.complete`; `score()` never awaits
anything, so making it `async` would be a lie about what it does — the
same reasoning `pipeline/retrieve/ranking.py`'s `build_context_bundle`
(also pure, also sync) already establishes for S5.

**This is also the ticket that finally reads `07` §5's degraded-mode
cap fields** (`SignalsForScoring.validation_component_cap`/`band_cap`,
built at T6.5) **and `critique.contracts.Critique`/the "no critique
happened" case** (built at T7.2) — both were left with no consumer until
now, disclosed as open items in `PROJECT-STATUS.md` §5 (items 23, 31) at
the time. `03` §S11's own formula never mentions either field by name —
it predates T6.5's degraded-mode design — so applying them here is this
ticket's own integration of `07` §5's stated intent ("validation
component capped at 0.55" / "0.35, band capped at low") into the formula
`03` actually specifies. Disclosed as an interpretation, not a literal
quote from either doc, in `PROJECT-STATUS.md`'s T7.3 section.

**A tri-state `regression_test_valid`/`test_pass_ratio` (T6.5) is treated
as `False`/`0.0` for scoring, not as a third value the formula branches
on.** `03` §S11's formula only ever multiplies/branches on a plain bool
or float — there is no "unknown" arm to that arithmetic. `None` means G4
or G6 never ran at all (a degraded-mode skip), which is not a claim of
correctness and gets no credit here, the same conservative reading T6.5's
own contract fields were built to make possible: "not proven" and "proven
false" are different claims upstream, but neither should raise a score."""

from __future__ import annotations

from typing import Literal

from roottrace_worker.pipeline.critique.contracts import Critique
from roottrace_worker.pipeline.reason.contracts import RootCauseAnalysis
from roottrace_worker.pipeline.retrieve.bundle import ContextBundle
from roottrace_worker.pipeline.score.contracts import (
    Band,
    Breakdown,
    ComponentBreakdown,
    ComponentName,
    ConfidenceScore,
    GateName,
    PublishMode,
)
from roottrace_worker.pipeline.understand.contracts import ErrorUnderstanding
from roottrace_worker.pipeline.validate.contracts import SignalsForScoring, ValidationResult

#: `03` §S11's own weights, in formula order.
_WEIGHTS: dict[ComponentName, float] = {
    "validation": 0.30,
    "critic": 0.20,
    "retrieval": 0.15,
    "evidence": 0.15,
    "self_assessment": 0.10,
    "historical": 0.10,
}

#: `06` §7.4: "V1 returns a constant 0.5 (no history yet)."
_HISTORICAL_COMPONENT_V1 = 0.5

_VERDICT_SCORE: dict[str, float] = {
    "approve": 1.00,
    "approve_with_notes": 0.80,
    "request_changes": 0.35,
    "reject": 0.00,
}

_BAND_SEVERITY: dict[Band, int] = {"insufficient": 0, "low": 1, "medium": 2, "high": 3}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _validation_component(signals: SignalsForScoring, *, attempts: int) -> float:
    regression_valid = bool(signals.regression_test_valid)
    raw = 0.30 if signals.build_passed else 0.00
    raw += 0.25 if regression_valid else 0.00
    raw += (signals.test_pass_ratio or 0.0) * 0.25
    raw += 0.10 if signals.new_static_findings_high == 0 else 0.00
    raw += 0.10 if signals.new_static_findings_medium == 0 else 0.05
    if attempts > 1:
        raw -= 0.05 * (attempts - 1)
    raw = _clamp(raw)
    if signals.validation_component_cap is not None:
        raw = min(raw, signals.validation_component_cap)
    return raw


def _critic_component(critique: Critique | None) -> float:
    """`None` — `03` §S10: "proceed to S11 with `critic_component = 0`"
    on a review that never happened, never silently treated as approval."""
    if critique is None:
        return 0.0
    raw = _VERDICT_SCORE[critique.verdict] * critique.agreement_with_diagnosis
    high_count = sum(1 for f in critique.findings if f.severity == "high")
    medium_count = sum(1 for f in critique.findings if f.severity == "medium")
    raw -= 0.15 * high_count
    raw -= 0.05 * medium_count
    return _clamp(raw)


def _retrieval_component(bundle: ContextBundle) -> float:
    return _clamp(bundle.quality.score - 0.05 * len(bundle.gaps))


def _cites_failure_point(analysis: RootCauseAnalysis, understanding: ErrorUnderstanding) -> bool:
    failure_point = understanding.failure_point
    if failure_point is None or failure_point.repo_path is None:
        return False
    return any(
        evidence.repo_path == failure_point.repo_path
        for step in analysis.reasoning_chain
        for evidence in step.evidence
    )


def _evidence_component(
    analysis: RootCauseAnalysis,
    *,
    understanding: ErrorUnderstanding,
    bundle: ContextBundle,
    dropped_claims: tuple[str, ...],
) -> float:
    kept = len(analysis.reasoning_chain)
    total = kept + len(dropped_claims)
    survived_fraction = (kept / total) if total else 1.0

    raw = survived_fraction * 0.50
    raw += 0.20 if _cites_failure_point(analysis, understanding) else 0.0
    raw += 0.15 if bundle.quality.signals.has_release_correlation else 0.0
    raw += 0.15 if analysis.root_cause.category != "external_dependency" else 0.0
    return _clamp(raw)


def _band(confidence: float) -> Band:
    if confidence >= 0.80:
        return "high"
    if confidence >= 0.60:
        return "medium"
    if confidence >= 0.40:
        return "low"
    return "insufficient"


def _apply_band_cap(band: Band, band_cap: Literal["low"] | None) -> Band:
    """`07` §5's degraded-mode table only ever names one band cap
    (`syntax_only` → `"low"`) — `SignalsForScoring.band_cap`'s own type is
    already narrowed to exactly that, so there is nothing further to
    validate here; this still compares severities rather than just
    returning `band_cap` outright, so a cap can only ever downgrade."""
    if band_cap is None:
        return band
    if _BAND_SEVERITY[band] > _BAND_SEVERITY[band_cap]:
        return band_cap
    return band


def _apply_hard_gates(
    confidence: float,
    *,
    signals: SignalsForScoring,
    critique: Critique | None,
    bundle: ContextBundle,
) -> tuple[float, tuple[GateName, ...], bool]:
    """`03` §S11's hard-gates table, applied in the table's own order.
    Returns `(confidence, gates_applied, never_publish)` — `never_publish`
    is a separate, explicit flag rather than something a caller re-derives
    from the numeric confidence alone: `03`'s own S8 row elsewhere in this
    doc says "hard gates cannot be bypassed by arithmetic," and a flag
    that does not depend on the arithmetic is what actually makes that
    true, rather than merely likely."""
    gates: list[GateName] = []
    never_publish = False

    if not signals.build_passed:
        confidence = 0.0
        gates.append("build_passed_false")
        never_publish = True

    if critique is not None and critique.verdict == "reject":
        confidence = min(confidence, 0.25)
        gates.append("critic_reject")
        never_publish = True

    if critique is not None and any(
        f.severity == "critical" and f.dimension == "security" for f in critique.findings
    ):
        confidence = 0.0
        gates.append("critical_security_finding")
        never_publish = True

    if not signals.regression_test_valid:  # False or None — neither is "proven valid"
        confidence = min(confidence, 0.50)
        gates.append("regression_test_invalid")

    if bundle.quality.score < 0.4:
        confidence = min(confidence, 0.45)
        gates.append("retrieval_quality_low")

    return _clamp(confidence), tuple(gates), never_publish


def _publish_mode(band: Band) -> PublishMode:
    if band in ("high", "medium"):
        return "open_pr"
    if band == "low":
        return "open_draft_pr"
    return "analysis_only"


_VERDICT_LABEL: dict[str, str] = {
    "approve": "approved it",
    "approve_with_notes": "approved it with non-blocking notes",
    "request_changes": "requested changes",
    "reject": "rejected it",
}


def _explanation(
    *,
    band: Band,
    signals: SignalsForScoring,
    critique: Critique | None,
    bundle: ContextBundle,
    gates_applied: tuple[GateName, ...],
) -> str:
    """Deterministically templated, not model-written — `03` §S11 has no
    LLM to ask for prose. Every sentence states a fact this function
    already computed or was handed; nothing here is invented the way a
    model's free-text summary could be."""
    band_label = {
        "high": "High confidence.",
        "medium": "Medium confidence.",
        "low": "Low confidence.",
        "insufficient": "Insufficient confidence.",
    }[band]
    parts = [band_label]

    if not signals.build_passed:
        parts.append("The patch did not build.")
    elif signals.regression_test_valid:
        parts.append(
            "The patch built cleanly, and the regression test correctly failed before "
            "the fix and passed after."
        )
    elif signals.regression_test_valid is None:
        parts.append(
            "The patch built cleanly; the regression-test gate did not run (degraded sandbox mode)."
        )
    else:
        parts.append(
            "The patch built, but the regression test did not prove the reported bug "
            "was actually reproduced."
        )

    if signals.test_pass_ratio is not None:
        parts.append(f"{signals.test_pass_ratio:.0%} of the existing test suite still passes.")

    if critique is None:
        parts.append("The independent review was unavailable.")
    else:
        parts.append(f"The independent reviewer {_VERDICT_LABEL[critique.verdict]}.")

    gap_note = f", with {len(bundle.gaps)} retrieval gap(s) noted" if bundle.gaps else ""
    parts.append(f"Retrieval quality scored {bundle.quality.score:.2f}{gap_note}.")

    if gates_applied:
        parts.append("Hard gate(s) applied: " + ", ".join(gates_applied) + ".")

    return " ".join(parts)


def score(
    *,
    validation: ValidationResult,
    attempts: int,
    critique: Critique | None,
    bundle: ContextBundle,
    understanding: ErrorUnderstanding,
    analysis: RootCauseAnalysis,
    dropped_claims: tuple[str, ...] = (),
    repo_opt_in: bool = False,
    path_matches: bool = False,
) -> ConfidenceScore:
    """Run S11 over one investigation's complete artefact set.

    `attempts` is caller-supplied (`repair.stage.repair`'s `attempt`
    parameter is the same kind of orchestration-owned counter this stage
    does not track for itself). `dropped_claims` is `reason.stage.
    ReasonOutcome.dropped_claims` — the S6 side-channel this formula's
    evidence component needs and `RootCauseAnalysis` alone does not carry,
    since a dropped claim is by definition not part of the kept analysis.
    `repo_opt_in`/`path_matches` are project-level configuration this
    stage has no access to on its own (`03`'s `auto_merge_eligible`:
    "requires repo opt-in AND path match AND band=high") — both default
    to `False`, so auto-merge is never eligible unless a caller that does
    have that configuration explicitly supplies both."""
    signals = validation.signals_for_scoring

    validation_raw = _validation_component(signals, attempts=attempts)
    critic_raw = _critic_component(critique)
    retrieval_raw = _retrieval_component(bundle)
    evidence_raw = _evidence_component(
        analysis, understanding=understanding, bundle=bundle, dropped_claims=dropped_claims
    )
    self_assessment_raw = analysis.self_assessed_confidence
    historical_raw = _HISTORICAL_COMPONENT_V1

    raws: dict[ComponentName, float] = {
        "validation": validation_raw,
        "critic": critic_raw,
        "retrieval": retrieval_raw,
        "evidence": evidence_raw,
        "self_assessment": self_assessment_raw,
        "historical": historical_raw,
    }
    confidence = _clamp(sum(_WEIGHTS[name] * raw for name, raw in raws.items()))

    confidence, gates_applied, never_publish = _apply_hard_gates(
        confidence, signals=signals, critique=critique, bundle=bundle
    )

    band = _apply_band_cap(_band(confidence), signals.band_cap)
    should_publish = band != "insufficient" and not never_publish
    publish_mode = _publish_mode(band)
    auto_merge_eligible = band == "high" and repo_opt_in and path_matches

    breakdown = Breakdown(
        **{
            name: ComponentBreakdown(
                weight=_WEIGHTS[name], raw=raw, contribution=_WEIGHTS[name] * raw
            )
            for name, raw in raws.items()
        }
    )

    return ConfidenceScore(
        confidence=confidence,
        band=band,
        breakdown=breakdown,
        gates_applied=gates_applied,
        explanation=_explanation(
            band=band,
            signals=signals,
            critique=critique,
            bundle=bundle,
            gates_applied=gates_applied,
        ),
        should_publish=should_publish,
        publish_mode=publish_mode,
        auto_merge_eligible=auto_merge_eligible,
    )
