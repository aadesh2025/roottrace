"""Evidence binding (`03` §S6 "Hard rule", `06` §4.2's S6 row, T5.3)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from roottrace_worker.github.types import Actor, Commit
from roottrace_worker.pipeline.reason.contracts import ReasoningStep
from roottrace_worker.pipeline.reason.extraction_schema import (
    ReasonEliminatedHypothesis,
    ReasonEvidence,
    ReasonFixStrategy,
    ReasonReply,
    ReasonRootCause,
    ReasonStep,
)
from roottrace_worker.pipeline.reason.validate import (
    evidence_is_bound,
    fix_strategy_is_grounded,
    primary_finding_survives,
    validate_eliminated_hypotheses,
    validate_reasoning_chain,
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

pytestmark = pytest.mark.unit

FILE_CONTENT = (
    "def calculate_total():\n    subtotal = base_price + tax_amount\n    return subtotal\n"
)

COMMIT = Commit(
    sha="8a3f1c2e" + "0" * 32,
    message="refactor: extract tax lookup",
    author=Actor(name="d", email="d@x.io"),
    date=datetime(2026, 7, 25, tzinfo=UTC),
)


def _bundle(**overrides: object) -> ContextBundle:
    base: dict[str, object] = {
        "bundle_id": "ctx_1",
        "repository": RepositoryRef(full_name="acme/checkout-api", ref="main"),
        "token_count": 100,
        "token_budget": 24_000,
        "files": (
            BundleFile(
                repo_path="services/checkout.py",
                strategy="frame_direct",
                relevance=1.0,
                language="python",
                content=FILE_CONTENT,
                line_range=(1, 3),
                truncated=False,
            ),
        ),
        "graph": BundleGraph(),
        "history": BundleHistory(blame_commit=COMMIT, recent_commits=(COMMIT,)),
        "tests": BundleTests(),
        "strategy_stats": {},
        "quality": Quality(
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
    }
    base.update(overrides)
    return ContextBundle(**base)  # type: ignore[arg-type]


BUNDLE = _bundle()


# ── evidence_is_bound: file ───────────────────────────────────────────────


def test_a_real_excerpt_at_a_real_line_range_binds() -> None:
    evidence = ReasonEvidence(
        kind="file",
        repo_path="services/checkout.py",
        line_range=(1, 2),
        excerpt="def calculate_total():",
    )
    assert evidence_is_bound(evidence, bundle=BUNDLE, breadcrumb_count=0)


def test_whitespace_differences_in_the_excerpt_still_bind() -> None:
    evidence = ReasonEvidence(
        kind="file",
        repo_path="services/checkout.py",
        line_range=(2, 2),
        excerpt="subtotal  =   base_price + tax_amount",
    )
    assert evidence_is_bound(evidence, bundle=BUNDLE, breadcrumb_count=0)


def test_a_fabricated_excerpt_does_not_bind() -> None:
    evidence = ReasonEvidence(
        kind="file",
        repo_path="services/checkout.py",
        line_range=(1, 2),
        excerpt="this line was never in the file",
    )
    assert not evidence_is_bound(evidence, bundle=BUNDLE, breadcrumb_count=0)


def test_a_path_never_retrieved_does_not_bind() -> None:
    evidence = ReasonEvidence(
        kind="file", repo_path="services/never_retrieved.py", line_range=(1, 2), excerpt="x"
    )
    assert not evidence_is_bound(evidence, bundle=BUNDLE, breadcrumb_count=0)


def test_a_line_range_outside_the_retrieved_window_does_not_bind() -> None:
    evidence = ReasonEvidence(
        kind="file",
        repo_path="services/checkout.py",
        line_range=(1, 500),
        excerpt="def calculate_total():",
    )
    assert not evidence_is_bound(evidence, bundle=BUNDLE, breadcrumb_count=0)


def test_file_evidence_missing_a_required_field_does_not_bind() -> None:
    evidence = ReasonEvidence(kind="file", repo_path="services/checkout.py")
    assert not evidence_is_bound(evidence, bundle=BUNDLE, breadcrumb_count=0)


# ── evidence_is_bound: breadcrumb / commit ────────────────────────────────


def test_a_breadcrumb_index_within_range_binds() -> None:
    evidence = ReasonEvidence(kind="breadcrumb", index=0)
    assert evidence_is_bound(evidence, bundle=BUNDLE, breadcrumb_count=2)


def test_a_breadcrumb_index_out_of_range_does_not_bind() -> None:
    evidence = ReasonEvidence(kind="breadcrumb", index=5)
    assert not evidence_is_bound(evidence, bundle=BUNDLE, breadcrumb_count=2)


def test_a_negative_breadcrumb_index_does_not_bind() -> None:
    evidence = ReasonEvidence(kind="breadcrumb", index=-1)
    assert not evidence_is_bound(evidence, bundle=BUNDLE, breadcrumb_count=2)


def test_a_real_commit_sha_binds() -> None:
    evidence = ReasonEvidence(kind="commit", sha=COMMIT.sha)
    assert evidence_is_bound(evidence, bundle=BUNDLE, breadcrumb_count=0)


def test_a_fabricated_commit_sha_does_not_bind() -> None:
    evidence = ReasonEvidence(kind="commit", sha="f" * 40)
    assert not evidence_is_bound(evidence, bundle=BUNDLE, breadcrumb_count=0)


# ── validate_reasoning_chain ───────────────────────────────────────────────


def test_a_step_with_no_evidence_is_kept_unconditionally() -> None:
    """A `hypothesise`-type step in `03` §S6's own worked example has no
    `evidence` key at all — speculative, not yet grounded, not thereby
    invalid."""
    step = ReasonStep(step=1, type="hypothesise", statement="a guess", prior=0.5)
    kept, dropped = validate_reasoning_chain([step], bundle=BUNDLE, breadcrumb_count=0)
    assert len(kept) == 1
    assert dropped == ()


def test_a_step_with_all_evidence_bound_is_kept() -> None:
    step = ReasonStep(
        step=1,
        type="observe",
        statement="x",
        evidence=[
            ReasonEvidence(
                kind="file",
                repo_path="services/checkout.py",
                line_range=(1, 1),
                excerpt="def calculate_total():",
            )
        ],
    )
    kept, dropped = validate_reasoning_chain([step], bundle=BUNDLE, breadcrumb_count=0)
    assert len(kept) == 1
    assert kept[0].evidence[0].repo_path == "services/checkout.py"
    assert dropped == ()


def test_a_step_with_any_unbound_evidence_is_dropped_entirely() -> None:
    step = ReasonStep(
        step=1,
        type="observe",
        statement="x",
        evidence=[
            ReasonEvidence(
                kind="file",
                repo_path="services/checkout.py",
                line_range=(1, 1),
                excerpt="def calculate_total():",
            ),
            ReasonEvidence(
                kind="file",
                repo_path="services/checkout.py",
                line_range=(1, 1),
                excerpt="fabricated",
            ),
        ],
    )
    kept, dropped = validate_reasoning_chain([step], bundle=BUNDLE, breadcrumb_count=0)
    assert kept == ()
    assert len(dropped) == 1
    assert "step=1" in dropped[0]


def test_valid_and_invalid_steps_are_judged_independently() -> None:
    good = ReasonStep(
        step=1,
        type="observe",
        statement="good",
        evidence=[
            ReasonEvidence(
                kind="file",
                repo_path="services/checkout.py",
                line_range=(1, 1),
                excerpt="def calculate_total():",
            )
        ],
    )
    bad = ReasonStep(
        step=2,
        type="observe",
        statement="bad",
        evidence=[
            ReasonEvidence(
                kind="file",
                repo_path="services/checkout.py",
                line_range=(1, 1),
                excerpt="fabricated",
            )
        ],
    )
    kept, dropped = validate_reasoning_chain([good, bad], bundle=BUNDLE, breadcrumb_count=0)
    assert len(kept) == 1
    assert kept[0].statement == "good"
    assert len(dropped) == 1


# ── validate_eliminated_hypotheses ─────────────────────────────────────────


def test_an_eliminated_hypothesis_with_bound_evidence_is_kept() -> None:
    item = ReasonEliminatedHypothesis(
        statement="wrong theory",
        eliminated_because="no support",
        evidence=[
            ReasonEvidence(
                kind="file",
                repo_path="services/checkout.py",
                line_range=(1, 1),
                excerpt="def calculate_total():",
            )
        ],
    )
    kept, dropped = validate_eliminated_hypotheses([item], bundle=BUNDLE, breadcrumb_count=0)
    assert len(kept) == 1
    assert dropped == ()


def test_an_eliminated_hypothesis_with_unbound_evidence_is_dropped() -> None:
    item = ReasonEliminatedHypothesis(
        statement="wrong theory",
        eliminated_because="no support",
        evidence=[
            ReasonEvidence(
                kind="file",
                repo_path="services/checkout.py",
                line_range=(1, 1),
                excerpt="fabricated",
            )
        ],
    )
    kept, dropped = validate_eliminated_hypotheses([item], bundle=BUNDLE, breadcrumb_count=0)
    assert kept == ()
    assert len(dropped) == 1


# ── fix_strategy_is_grounded / primary_finding_survives ────────────────────


def test_fix_strategy_targeting_only_retrieved_files_is_grounded() -> None:
    fix_strategy = ReasonFixStrategy(approach="a", files_to_modify=["services/checkout.py"])
    assert fix_strategy_is_grounded(fix_strategy, bundle=BUNDLE)


def test_fix_strategy_targeting_an_unretrieved_file_is_not_grounded() -> None:
    fix_strategy = ReasonFixStrategy(approach="a", files_to_modify=["services/never_retrieved.py"])
    assert not fix_strategy_is_grounded(fix_strategy, bundle=BUNDLE)


def test_primary_finding_needs_a_surviving_conclude_step_and_a_grounded_fix() -> None:
    kept_steps = (ReasoningStep(step=1, type="conclude", statement="root cause"),)
    reply = ReasonReply(
        root_cause=ReasonRootCause(summary="s", mechanism="m", category="other"),
        fix_strategy=ReasonFixStrategy(approach="a", files_to_modify=["services/checkout.py"]),
    )
    assert primary_finding_survives(kept_steps, reply=reply, bundle=BUNDLE)


def test_primary_finding_fails_with_no_conclude_step() -> None:
    kept_steps = (ReasoningStep(step=1, type="observe", statement="x"),)
    reply = ReasonReply(
        root_cause=ReasonRootCause(summary="s", mechanism="m", category="other"),
        fix_strategy=ReasonFixStrategy(approach="a", files_to_modify=["services/checkout.py"]),
    )
    assert not primary_finding_survives(kept_steps, reply=reply, bundle=BUNDLE)


def test_primary_finding_fails_with_an_unretrieved_fix_target() -> None:
    kept_steps = (ReasoningStep(step=1, type="conclude", statement="root cause"),)
    reply = ReasonReply(
        root_cause=ReasonRootCause(summary="s", mechanism="m", category="other"),
        fix_strategy=ReasonFixStrategy(
            approach="a", files_to_modify=["services/never_retrieved.py"]
        ),
    )
    assert not primary_finding_survives(kept_steps, reply=reply, bundle=BUNDLE)
