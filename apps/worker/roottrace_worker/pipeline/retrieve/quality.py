"""`quality.score` (`03` §S5's output contract, T4.4).

`03` §S5 fixes the *signal fields* (`failure_point_resolved`,
`entry_point_resolved`, `callees_resolved`, `callers_resolved`, `has_tests`,
`has_release_correlation`, `unresolved_symbols`) but not the formula that
turns them into `score` — that is this stage's to define. `03` §S11 only
consumes `quality.score` as an opaque input (weight 0.15 in the final
confidence, and a hard gate: below 0.4 caps confidence at 0.45) and applies
its *own* penalty for `gaps` entries there, so nothing here needs to match a
formula mandated elsewhere; it only needs to be a defensible, monotonic
measure of "how much of what retrieval was supposed to find did it find."

**Weights sum to 1.0** for the same reason `severity_factors` in ingest's
`triage.py` does: it is what makes the result a fraction with a stable
meaning across investigations, not an arbitrary number whose scale drifts as
signals are added or removed.

**`failure_point_resolved` and `entry_point_resolved` carry the most weight**
because they are the two priorities `03` §S5's eviction table marks
non-negotiable (priority 1 and 2) — a bundle missing either is missing the
thing the whole investigation is about, and no amount of tests or history
should be able to compensate for that in the score.
"""

from __future__ import annotations

from roottrace_worker.pipeline.retrieve.bundle import QualitySignals

WEIGHT_FAILURE_POINT = 0.30
WEIGHT_ENTRY_POINT = 0.15
WEIGHT_CALLEES = 0.20
WEIGHT_CALLERS = 0.10
WEIGHT_TESTS = 0.15
WEIGHT_RELEASE_CORRELATION = 0.10

#: Per unresolved symbol S4 asked for and nothing could locate. Small and
#: capped (see `score`) — one unresolved name in an otherwise-complete bundle
#: should read as "slightly incomplete," not "worthless."
UNRESOLVED_SYMBOL_PENALTY = 0.05
MAX_UNRESOLVED_PENALTY = 0.20

#: `callees_resolved`/`callers_resolved` are counts, not booleans. A count of
#: 0 contributes nothing; anything at or above this is treated as "as
#: resolved as this signal can meaningfully get" — a function that calls
#: fifteen things is not proportionally better understood than one that
#: calls three, and a linear count would let a large, unrelated function
#: dominate the score.
COUNT_SATURATION = 3


def _saturating_fraction(count: int) -> float:
    if count <= 0:
        return 0.0
    return min(1.0, count / COUNT_SATURATION)


def compute_score(signals: QualitySignals) -> float:
    """The weighted signals, minus a small, capped penalty for unresolved
    symbols, clamped to `[0, 1]`."""
    score = (
        WEIGHT_FAILURE_POINT * (1.0 if signals.failure_point_resolved else 0.0)
        + WEIGHT_ENTRY_POINT * (1.0 if signals.entry_point_resolved else 0.0)
        + WEIGHT_CALLEES * _saturating_fraction(signals.callees_resolved)
        + WEIGHT_CALLERS * _saturating_fraction(signals.callers_resolved)
        + WEIGHT_TESTS * (1.0 if signals.has_tests else 0.0)
        + WEIGHT_RELEASE_CORRELATION * (1.0 if signals.has_release_correlation else 0.0)
    )
    penalty = min(
        MAX_UNRESOLVED_PENALTY, UNRESOLVED_SYMBOL_PENALTY * len(signals.unresolved_symbols)
    )
    return round(max(0.0, min(1.0, score - penalty)), 4)
