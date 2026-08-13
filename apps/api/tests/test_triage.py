"""Triage (T2.4, `03` §S3).

T2.4's acceptance, both halves:

- the score matches hand-computed values **at every band boundary**
- each of the **six** gate reasons is individually reachable and correctly
  reported

The first is tested by arithmetic done by hand in the test, not by calling the
implementation and asserting it equals itself. The second is tested one reason
at a time, with a positive control that ungated input actually investigates —
a gate that always refused would satisfy every "is gated" assertion on its own.
"""

from __future__ import annotations

from typing import Any

import pytest

from roottrace_api.ingest.triage import (
    BAND_P0,
    BAND_P1,
    BAND_P2,
    GateReason,
    as_output,
    endpoint_criticality,
    evaluate_gate,
    severity_band,
    severity_factors,
    triage,
)

pytestmark = pytest.mark.unit


def ungated(**overrides: Any) -> dict[str, Any]:
    """Input that passes every gate, so a test can change one thing."""
    base: dict[str, Any] = {
        "rate_per_hour": 500.0,
        "affected_users": 1000,
        "route_pattern": "/api/v2/checkout",
        "environment": "production",
        "is_new_issue": True,
        "criticality_config": {"/api/v2/checkout": 1.0},
    }
    base.update(overrides)
    return base


# ── The arithmetic, computed by hand ───────────────────────────────────────


def test_the_maximum_score_is_exactly_one() -> None:
    """Every weight at full: 0.30 + 0.25 + 0.20 + 0.15 + 0.10.

    That the weights sum to 1.0 is what makes the score a fraction rather than
    an arbitrary number, and what makes the band thresholds comparable across
    projects.
    """
    factors = severity_factors(
        rate_per_hour=500,
        affected_users=1000,
        route_pattern="/api/v2/checkout",
        environment="production",
        is_new_issue=True,
        criticality_config={"/api/v2/checkout": 1.0},
    )

    assert factors.rate == pytest.approx(0.30)
    assert factors.users == pytest.approx(0.25)
    assert factors.criticality == pytest.approx(0.20)
    assert factors.environment == pytest.approx(0.15)
    assert factors.novelty == pytest.approx(0.10)
    assert factors.total == pytest.approx(1.0)


def test_the_minimum_score_is_not_zero() -> None:
    """A development-environment repeat of a known issue on an uncritical route
    still scores: 0 + 0 + 0.20*0.0 + 0.15*0.1 + 0.10*0.2 = 0.035."""
    factors = severity_factors(
        rate_per_hour=0,
        affected_users=0,
        route_pattern="/health",
        environment="development",
        is_new_issue=False,
        criticality_config={"/health": 0.0},
    )
    assert factors.total == pytest.approx(0.035)


def test_the_documented_example_reproduces() -> None:
    """`03` §S3's own output block: rate 0.24, users 0.18, criticality 0.20,
    environment 0.15, novelty 0.02 → 0.79.

    Working backwards: rate 0.24/0.30 = 0.8 of the ceiling = 400/hour; users
    0.18/0.25 = 0.72 = 720.
    """
    factors = severity_factors(
        rate_per_hour=400,
        affected_users=720,
        route_pattern="/api/v2/checkout",
        environment="production",
        is_new_issue=False,
        criticality_config={"/api/v2/checkout": 1.0},
    )

    assert factors.as_record() == {
        "rate": 0.24,
        "users": 0.18,
        "criticality": 0.2,
        "environment": 0.15,
        "novelty": 0.02,
    }
    assert factors.total == pytest.approx(0.79)


def test_a_storm_is_clamped_rather_than_unbounded() -> None:
    """Ten thousand an hour is not twenty times worse than five hundred. Left
    unclamped this factor would drown every other signal."""
    hot = severity_factors(
        rate_per_hour=10_000,
        affected_users=50_000,
        route_pattern=None,
        environment="production",
        is_new_issue=True,
    )
    assert hot.rate == pytest.approx(0.30)
    assert hot.users == pytest.approx(0.25)


# ── Every band boundary, at its exact value ────────────────────────────────


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (1.0, "P0"),
        (BAND_P0, "P0"),
        (BAND_P0 - 0.0001, "P1"),
        (BAND_P1, "P1"),
        (BAND_P1 - 0.0001, "P2"),
        (BAND_P2, "P2"),
        (BAND_P2 - 0.0001, "P3"),
        (0.0, "P3"),
    ],
)
def test_every_band_boundary(score: float, expected: str) -> None:
    """T2.4 verbatim. Each threshold is asserted at exactly its value and one
    ten-thousandth below it — an off-by-one comparison passes a sampled test
    and fails here."""
    assert severity_band(score) == expected


def test_the_bands_are_inclusive_lower_bounds() -> None:
    assert severity_band(BAND_P0) == "P0"
    assert severity_band(BAND_P1) == "P1"
    assert severity_band(BAND_P2) == "P2"


# ── Endpoint criticality ───────────────────────────────────────────────────


def test_criticality_defaults_to_half_when_unconfigured() -> None:
    assert endpoint_criticality("/api/v2/anything", None) == 0.5
    assert endpoint_criticality("/api/v2/anything", {"/other": 1.0}) == 0.5


def test_an_exact_route_beats_a_glob() -> None:
    config = {"/api/v2/auth/*": 0.9, "/api/v2/auth/token": 0.3}
    assert endpoint_criticality("/api/v2/auth/token", config) == 0.3


def test_the_longest_matching_glob_wins() -> None:
    """So a broad pattern someone adds later cannot quietly downgrade a
    specific one that was already there."""
    config = {"/api/*": 0.2, "/api/v2/auth/*": 0.9}
    assert endpoint_criticality("/api/v2/auth/token", config) == 0.9


def test_a_health_check_can_be_scored_to_zero() -> None:
    """`03` §S3's own example. A noisy health endpoint should not be able to
    reach P0 on rate alone."""
    assert endpoint_criticality("/health", {"/health": 0.0}) == 0.0


def test_an_absent_route_uses_the_default() -> None:
    assert endpoint_criticality(None, {"/api": 1.0}) == 0.5


# ── The six gate reasons, one at a time ────────────────────────────────────


def test_ungated_input_investigates() -> None:
    """The positive control. Every "is gated" assertion below means nothing if
    nothing ever investigates."""
    result = triage(**ungated())

    assert result.should_investigate
    assert result.gate_reason is None
    assert result.severity == "P0"


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"has_active_investigation": True}, GateReason.ALREADY_INVESTIGATING),
        ({"issue_muted": True}, GateReason.MUTED),
        ({"environment": "staging"}, GateReason.ENVIRONMENT_EXCLUDED),
        ({"within_cooldown": True}, GateReason.COOLDOWN_ACTIVE),
        ({"quota_remaining": 0}, GateReason.QUOTA_EXHAUSTED),
    ],
)
def test_each_gate_reason_is_individually_reachable(
    override: dict[str, Any], expected: GateReason
) -> None:
    """Five of the six, each reached by changing exactly one input from a
    baseline that would otherwise investigate."""
    result = triage(**ungated(**override))

    assert not result.should_investigate
    assert result.gate_reason is expected


def test_below_min_severity_is_reachable() -> None:
    """The sixth. Needs a low score rather than a flag, so it is built rather
    than toggled: a quiet, known issue on an uncritical route in production."""
    result = triage(
        **ungated(
            rate_per_hour=1,
            affected_users=0,
            is_new_issue=False,
            route_pattern="/health",
            criticality_config={"/health": 0.0},
        )
    )

    assert result.severity == "P3"
    assert result.gate_reason is GateReason.BELOW_MIN_SEVERITY


def test_all_six_reasons_are_covered_by_the_tests_above() -> None:
    """A guard against the enum growing a seventh reason nobody tests."""
    assert {reason.value for reason in GateReason} == {
        "already_investigating",
        "cooldown_active",
        "below_min_severity",
        "quota_exhausted",
        "environment_excluded",
        "muted",
    }


def test_an_active_investigation_is_reported_before_anything_else() -> None:
    """Order is deliberate. An occurrence that lost the insert race is
    `already_investigating` whatever else is also true — that is the outcome
    B8 makes indistinguishable from having been gated."""
    reason = evaluate_gate(
        severity="P3",
        environment="staging",
        has_active_investigation=True,
        within_cooldown=True,
        quota_remaining=0,
        issue_muted=True,
    )
    assert reason is GateReason.ALREADY_INVESTIGATING


def test_muted_is_reported_before_severity() -> None:
    """A user muted this deliberately, and that is the reason they would look
    for — not a threshold they never set."""
    reason = evaluate_gate(
        severity="P3",
        environment="production",
        has_active_investigation=False,
        within_cooldown=False,
        quota_remaining=1,
        issue_muted=True,
    )
    assert reason is GateReason.MUTED


# ── Configuration ──────────────────────────────────────────────────────────


def test_a_project_can_lower_its_minimum_severity() -> None:
    quiet = ungated(
        rate_per_hour=1,
        affected_users=0,
        is_new_issue=False,
        route_pattern="/health",
        criticality_config={"/health": 0.0},
    )

    assert triage(**quiet).gate_reason is GateReason.BELOW_MIN_SEVERITY
    assert triage(**quiet, min_investigation_severity="P3").should_investigate


def test_a_project_can_investigate_staging() -> None:
    staging = ungated(environment="staging")

    assert triage(**staging).gate_reason is GateReason.ENVIRONMENT_EXCLUDED
    assert triage(**staging, investigated_environments=("production", "staging")).should_investigate


def test_staging_scores_lower_than_production_for_the_same_error() -> None:
    """Weighted, not filtered. The environment gate is a separate decision from
    the score, and both exist."""
    production = severity_factors(
        rate_per_hour=100,
        affected_users=100,
        route_pattern="/api/v2/checkout",
        environment="production",
        is_new_issue=True,
    )
    staging = severity_factors(
        rate_per_hour=100,
        affected_users=100,
        route_pattern="/api/v2/checkout",
        environment="staging",
        is_new_issue=True,
    )
    assert staging.total < production.total


# ── The output contract ────────────────────────────────────────────────────


def test_the_output_matches_the_documented_shape() -> None:
    output = as_output(triage(**ungated()), investigation_id="inv_01J2K")

    assert set(output) == {
        "severity",
        "severity_score",
        "severity_factors",
        "should_investigate",
        "gate_reason",
        "investigation_id",
    }
    assert set(output["severity_factors"]) == {
        "rate",
        "users",
        "criticality",
        "environment",
        "novelty",
    }


def test_a_gated_result_reports_its_reason_as_a_plain_string() -> None:
    """The value is stored and shown in the dashboard; an enum repr would leak
    a Python type name into the product."""
    output = as_output(triage(**ungated(issue_muted=True)))

    assert output["gate_reason"] == "muted"
    assert output["should_investigate"] is False
    assert output["investigation_id"] is None


def test_the_factors_sum_to_the_reported_score() -> None:
    """Otherwise the dashboard's breakdown and its headline number disagree,
    and neither can be trusted."""
    result = triage(**ungated(rate_per_hour=137, affected_users=412))
    assert sum(result.factors.as_record().values()) == pytest.approx(
        result.severity_score, abs=0.001
    )
