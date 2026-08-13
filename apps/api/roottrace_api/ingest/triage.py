"""Triage — severity and the investigation gate (`03` §S3).

Two decisions: how bad is this, and does it deserve a pipeline run of its own.

**The gate is advisory; the database is authoritative (B8).** Every condition
below is a *read*. Two occurrences of the same fingerprint arriving in the same
instant both evaluate the gate, both see no active investigation, and both
proceed — two pipelines, two PRs, double cost, one bug. Reads cannot enforce
mutual exclusion, so S3 always attempts the insert and handles the conflict
against the partial unique index in `04` §8.

The loser of that race is **not** an error. It attaches to the winner exactly as
a gated occurrence does, and the outcome is indistinguishable from having been
gated in the first place — which is the point.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from fnmatch import fnmatch
from typing import Any

#: `03` §S3. The weights sum to 1.0, which is what makes the score a fraction
#: rather than an arbitrary number — and what makes the band thresholds mean
#: something across projects.
WEIGHT_RATE = 0.30
WEIGHT_USERS = 0.25
WEIGHT_CRITICALITY = 0.20
WEIGHT_ENVIRONMENT = 0.15
WEIGHT_NOVELTY = 0.10

RATE_CEILING = 500.0
USERS_CEILING = 1000.0

#: Weighted, not filtered. A staging error still scores — it just scores less.
ENVIRONMENT_WEIGHT: dict[str, float] = {
    "production": 1.0,
    "staging": 0.4,
    "development": 0.1,
    "test": 0.1,
}

DEFAULT_CRITICALITY = 0.5

NOVELTY_NEW = 1.0
NOVELTY_SEEN = 0.2

#: `03` §S3 bands, inclusive lower bounds.
BAND_P0 = 0.80
BAND_P1 = 0.60
BAND_P2 = 0.35

SEVERITY_ORDER = ("P3", "P2", "P1", "P0")


class GateReason(StrEnum):
    """Why a pipeline was not launched. `03` §S3 names exactly these six."""

    ALREADY_INVESTIGATING = "already_investigating"
    COOLDOWN_ACTIVE = "cooldown_active"
    BELOW_MIN_SEVERITY = "below_min_severity"
    QUOTA_EXHAUSTED = "quota_exhausted"
    ENVIRONMENT_EXCLUDED = "environment_excluded"
    MUTED = "muted"


@dataclass(frozen=True, slots=True)
class SeverityFactors:
    rate: float
    users: float
    criticality: float
    environment: float
    novelty: float

    def as_record(self) -> dict[str, float]:
        return {
            "rate": round(self.rate, 4),
            "users": round(self.users, 4),
            "criticality": round(self.criticality, 4),
            "environment": round(self.environment, 4),
            "novelty": round(self.novelty, 4),
        }

    @property
    def total(self) -> float:
        return self.rate + self.users + self.criticality + self.environment + self.novelty


@dataclass(frozen=True, slots=True)
class Triage:
    severity: str
    severity_score: float
    factors: SeverityFactors
    should_investigate: bool
    gate_reason: GateReason | None


def _normalise(value: float, ceiling: float) -> float:
    """Linear 0..1, clamped. A ten-thousand-per-hour storm is not twenty times
    worse than a five-hundred-per-hour one — both are as bad as this factor
    can express, and letting the score run past 1.0 would drown every other
    signal."""
    if ceiling <= 0:
        return 0.0
    return max(0.0, min(1.0, value / ceiling))


def endpoint_criticality(route: str | None, config: Mapping[str, float] | None) -> float:
    """Per-project, defaulting to 0.5, with glob support (`03` §S3).

    Longest matching pattern wins, so `/api/v2/auth/token` takes the value of
    `/api/v2/auth/*` rather than of a shorter `/api/*` someone added later.
    """
    if not route or not config:
        return DEFAULT_CRITICALITY

    if route in config:
        return float(config[route])

    matches = [
        (pattern, value)
        for pattern, value in config.items()
        if "*" in pattern and fnmatch(route, pattern)
    ]
    if not matches:
        return DEFAULT_CRITICALITY
    return float(max(matches, key=lambda item: len(item[0]))[1])


def severity_factors(
    *,
    rate_per_hour: float,
    affected_users: int,
    route_pattern: str | None,
    environment: str,
    is_new_issue: bool,
    criticality_config: Mapping[str, float] | None = None,
) -> SeverityFactors:
    return SeverityFactors(
        rate=WEIGHT_RATE * _normalise(rate_per_hour, RATE_CEILING),
        users=WEIGHT_USERS * _normalise(affected_users, USERS_CEILING),
        criticality=WEIGHT_CRITICALITY * endpoint_criticality(route_pattern, criticality_config),
        environment=WEIGHT_ENVIRONMENT * ENVIRONMENT_WEIGHT.get(environment, 0.1),
        novelty=WEIGHT_NOVELTY * (NOVELTY_NEW if is_new_issue else NOVELTY_SEEN),
    )


def severity_band(score: float) -> str:
    """`03` §S3's bands. Lower bounds are inclusive — a score of exactly 0.80
    is P0, and the boundary tests assert each one at its exact value."""
    if score >= BAND_P0:
        return "P0"
    if score >= BAND_P1:
        return "P1"
    if score >= BAND_P2:
        return "P2"
    return "P3"


def meets_minimum(severity: str, minimum: str) -> bool:
    return SEVERITY_ORDER.index(severity) >= SEVERITY_ORDER.index(minimum)


def evaluate_gate(
    *,
    severity: str,
    environment: str,
    has_active_investigation: bool,
    within_cooldown: bool,
    quota_remaining: int,
    issue_muted: bool,
    min_investigation_severity: str = "P2",
    investigated_environments: tuple[str, ...] = ("production",),
) -> GateReason | None:
    """Which gate, if any, stops this occurrence launching a pipeline.

    Order matters and is not arbitrary: the cheapest and most certain reasons
    are checked first, and the reported reason is the *first* that applies. An
    occurrence that is both muted and below severity reports `muted`, because
    that is the one a user set deliberately and the one they would look for.
    """
    if has_active_investigation:
        return GateReason.ALREADY_INVESTIGATING
    if issue_muted:
        return GateReason.MUTED
    if environment not in investigated_environments:
        return GateReason.ENVIRONMENT_EXCLUDED
    if not meets_minimum(severity, min_investigation_severity):
        return GateReason.BELOW_MIN_SEVERITY
    if within_cooldown:
        return GateReason.COOLDOWN_ACTIVE
    if quota_remaining <= 0:
        return GateReason.QUOTA_EXHAUSTED
    return None


def triage(
    *,
    rate_per_hour: float,
    affected_users: int,
    route_pattern: str | None,
    environment: str,
    is_new_issue: bool,
    has_active_investigation: bool = False,
    within_cooldown: bool = False,
    quota_remaining: int = 1,
    issue_muted: bool = False,
    criticality_config: Mapping[str, float] | None = None,
    min_investigation_severity: str = "P2",
    investigated_environments: tuple[str, ...] = ("production",),
) -> Triage:
    """Score the occurrence and decide whether it launches a pipeline.

    A gated occurrence still attaches to its issue and appears in analytics
    (`03` §S3) — the gate withholds a pipeline run, not the data.
    """
    factors = severity_factors(
        rate_per_hour=rate_per_hour,
        affected_users=affected_users,
        route_pattern=route_pattern,
        environment=environment,
        is_new_issue=is_new_issue,
        criticality_config=criticality_config,
    )
    score = factors.total
    severity = severity_band(score)

    reason = evaluate_gate(
        severity=severity,
        environment=environment,
        has_active_investigation=has_active_investigation,
        within_cooldown=within_cooldown,
        quota_remaining=quota_remaining,
        issue_muted=issue_muted,
        min_investigation_severity=min_investigation_severity,
        investigated_environments=investigated_environments,
    )

    return Triage(
        severity=severity,
        severity_score=round(score, 3),
        factors=factors,
        should_investigate=reason is None,
        gate_reason=reason,
    )


def as_output(result: Triage, investigation_id: str | None = None) -> dict[str, Any]:
    """`03` §S3's output contract."""
    return {
        "severity": result.severity,
        "severity_score": result.severity_score,
        "severity_factors": result.factors.as_record(),
        "should_investigate": result.should_investigate,
        "gate_reason": result.gate_reason.value if result.gate_reason else None,
        "investigation_id": investigation_id,
    }
