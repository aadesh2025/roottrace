"""`quality.score` (`03` §S5's output contract, T4.4).

`03` §S5 fixes the signal fields; the formula turning them into a score is
this stage's own to define (`06`/`03` §S11 consume `score` as an opaque
input) — see `quality.py`'s module docstring for the reasoning behind the
weights chosen here.
"""

from __future__ import annotations

import pytest

from roottrace_worker.pipeline.retrieve.bundle import QualitySignals
from roottrace_worker.pipeline.retrieve.quality import (
    MAX_UNRESOLVED_PENALTY,
    compute_score,
)

pytestmark = pytest.mark.unit


def _signals(**overrides: object) -> QualitySignals:
    base: dict[str, object] = {
        "failure_point_resolved": False,
        "entry_point_resolved": False,
        "callees_resolved": 0,
        "callers_resolved": 0,
        "has_tests": False,
        "has_release_correlation": False,
        "unresolved_symbols": (),
    }
    base.update(overrides)
    return QualitySignals(**base)  # type: ignore[arg-type]


def test_every_signal_true_and_saturated_scores_1() -> None:
    signals = _signals(
        failure_point_resolved=True,
        entry_point_resolved=True,
        callees_resolved=10,
        callers_resolved=10,
        has_tests=True,
        has_release_correlation=True,
    )
    assert compute_score(signals) == 1.0


def test_every_signal_false_scores_0() -> None:
    assert compute_score(_signals()) == 0.0


def test_the_failure_point_carries_the_most_weight() -> None:
    """Priority 1 in `03` §S5's eviction table is "non-negotiable"; the score
    formula reflects that by weighting it above every other single signal."""
    only_failure_point = compute_score(_signals(failure_point_resolved=True))
    only_tests = compute_score(_signals(has_tests=True))
    only_release = compute_score(_signals(has_release_correlation=True))
    assert only_failure_point > only_tests
    assert only_failure_point > only_release


def test_callee_and_caller_counts_saturate_rather_than_scale_linearly() -> None:
    """A function that calls fifteen things is not proportionally better
    understood than one that calls three — both should score the same once
    past the saturation point."""
    three = compute_score(_signals(callees_resolved=3))
    fifteen = compute_score(_signals(callees_resolved=15))
    assert three == fifteen


def test_a_single_callee_scores_less_than_three() -> None:
    one = compute_score(_signals(callees_resolved=1))
    three = compute_score(_signals(callees_resolved=3))
    assert 0.0 < one < three


def test_unresolved_symbols_reduce_the_score() -> None:
    clean = compute_score(_signals(failure_point_resolved=True))
    with_gaps = compute_score(_signals(failure_point_resolved=True, unresolved_symbols=("a", "b")))
    assert with_gaps < clean


def test_the_unresolved_penalty_is_capped() -> None:
    """A long list of unresolved symbols should not be able to drive the
    score to zero on its own — it is one factor among several, not a veto."""
    many_gaps = compute_score(
        _signals(failure_point_resolved=True, unresolved_symbols=tuple(f"s{n}" for n in range(20)))
    )
    floor = compute_score(_signals(failure_point_resolved=True)) - MAX_UNRESOLVED_PENALTY
    assert many_gaps == pytest.approx(floor, abs=1e-6)


def test_the_score_never_leaves_zero_to_one() -> None:
    assert 0.0 <= compute_score(_signals()) <= 1.0
    assert (
        0.0
        <= compute_score(
            _signals(
                failure_point_resolved=True,
                entry_point_resolved=True,
                callees_resolved=100,
                callers_resolved=100,
                has_tests=True,
                has_release_correlation=True,
            )
        )
        <= 1.0
    )
