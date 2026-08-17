"""T4.1 acceptance — S4 over the 25-case corpus (`15` §6).

> **Accept:** All 25 fixture errors produce a valid `ErrorUnderstanding`. Frame
> paths resolve correctly for ≥ 22/25. Exception family is correct for ≥ 23/25.

Three numbers, measured against ground truth in the case files rather than
against the code's own output — `expected.exception_family` and
`expected.frame_repo_paths` were written by reading each error, and every path
in them is checked below to be a file that really exists in the synthetic
repository. A ground truth derived from the resolver would make this test a
tautology.

**The two families this misses are recorded, not tuned away.** `race-01` is a
lost update and `resource-01` is unbounded growth; both raise an ordinary
`ValueError` whose text says nothing about concurrency or memory, and both are
knowable only from breadcrumbs. `taxonomy.classify` deliberately does not read
breadcrumbs (see its module docstring), so the deterministic pass scores 23/25
— the bar exactly, with no margin. The extractor closes the gap at T5.2, and
this file is where that improvement will show up.

No database, no network, no LLM. Marked `integration` to sit with the other
corpus tests, which read the same files.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from fixtures.triggers.cases import CASE_IDS
from roottrace_worker.pipeline.understand import (
    ErrorUnderstanding,
    ExceptionFamily,
    Flag,
    PathMapping,
    understand,
)

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REPO = REPO_ROOT / "fixtures" / "synthetic-repo"
CORPUS = REPO_ROOT / "fixtures" / "error-corpus"

#: `15` §6's acceptance thresholds, out of 25.
MIN_FRAME_PATHS = 22
MIN_FAMILIES = 23

#: The one case whose frames need cascade step 3 (suffix matching against the
#: repository tree), which is S5's and belongs to T4.2 — S4 has no repo access
#: (`03` §8.1). Named here so that a *second* case starting to fail is a
#: visible regression rather than absorbed by the threshold.
NEEDS_TREE_SEARCH = {"config-02"}

#: The two cases whose family is only knowable from breadcrumbs.
NEEDS_BREADCRUMB_READING = {"race-01", "resource-01"}


def case(case_id: str) -> dict[str, Any]:
    return json.loads((CORPUS / f"{case_id}.case.json").read_text(encoding="utf-8"))


def payload(case_id: str) -> dict[str, Any]:
    return json.loads((CORPUS / f"{case_id}.json").read_text(encoding="utf-8"))


def project_mappings() -> tuple[PathMapping, ...]:
    """The fixture project's configured mappings, from the repository itself
    (`A1` §7) rather than restated here."""
    metadata = json.loads((FIXTURE_REPO / ".roottrace-fixture.json").read_text(encoding="utf-8"))
    return tuple(
        PathMapping(mapping["from"], mapping["to"]) for mapping in metadata["path_mappings"]
    )


MAPPINGS = project_mappings()


def understanding_for(case_id: str) -> ErrorUnderstanding:
    event = payload(case_id)["events"][0]
    return asyncio.run(understand(event, mappings=MAPPINGS)).understanding


# ── Ground truth is real ───────────────────────────────────────────────────


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_ground_truth_frame_paths_are_real_files(case_id: str) -> None:
    """Guards the guard. If a fixture file moves, this fails before the
    resolution score silently drops."""
    for repo_path in case(case_id)["expected"]["frame_repo_paths"]:
        assert (FIXTURE_REPO / repo_path).is_file(), f"{case_id}: {repo_path} is not a file"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_ground_truth_family_is_a_real_family(case_id: str) -> None:
    family = case(case_id)["expected"]["exception_family"]
    assert family in set(ExceptionFamily)
    assert family != ExceptionFamily.UNCLASSIFIED


# ── Criterion 1: all 25 produce a valid ErrorUnderstanding ─────────────────


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_case_produces_a_valid_understanding(case_id: str) -> None:
    understanding = understanding_for(case_id)

    assert understanding.language == "python"
    assert understanding.framework == "fastapi"
    assert understanding.exception.type == payload(case_id)["events"][0]["error"]["type"]
    assert understanding.frames, "every corpus payload carries frames"
    assert understanding.failure_point is not None
    assert understanding.entry_point is not None
    assert understanding.extraction_confidence == 0.5
    assert Flag.DETERMINISTIC_ONLY in understanding.flags


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_case_produces_a_usable_retrieval_plan(case_id: str) -> None:
    """`03` §S4: *this stage decides what stage 5 will go and fetch.* A plan
    with nothing to fetch would make S5 terminate as `insufficient_context`
    for a reason that was S4's fault."""
    plan = understanding_for(case_id).retrieval_plan
    assert plan.must_fetch or case_id in NEEDS_TREE_SEARCH
    assert plan.want_tests_for
    assert plan.semantic_queries


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_planned_path_is_repo_relative(case_id: str) -> None:
    """S5 fetches these literally. An absolute path would be fetched from the
    repository root and quietly return nothing."""
    plan = understanding_for(case_id).retrieval_plan
    for path in (*plan.must_fetch, *plan.want_git_history_for):
        assert not path.startswith("/")
        assert ".." not in path


# ── Criterion 2: frame paths resolve for ≥ 22/25 ───────────────────────────


def resolved_frame_paths(case_id: str) -> list[str]:
    return [
        frame.repo_path
        for frame in understanding_for(case_id).in_app_frames
        if frame.repo_path is not None
    ]


def test_frame_paths_resolve_for_at_least_22_of_25() -> None:
    correct = [
        case_id
        for case_id in CASE_IDS
        if resolved_frame_paths(case_id) == case(case_id)["expected"]["frame_repo_paths"]
    ]
    wrong = sorted(set(CASE_IDS) - set(correct))
    assert len(correct) >= MIN_FRAME_PATHS, f"{len(correct)}/25; missed {wrong}"
    # Stated as an equality so that a case *starting* to fail is visible even
    # though the threshold would still be met.
    assert wrong == sorted(NEEDS_TREE_SEARCH), f"unexpected resolution failures: {wrong}"


@pytest.mark.parametrize("case_id", sorted(set(CASE_IDS) - NEEDS_TREE_SEARCH))
def test_every_resolved_path_exists_in_the_repository(case_id: str) -> None:
    """The check that cannot drift. A resolver that produced a plausible wrong
    path would pass a comparison against ground truth written from the same
    assumption; it cannot pass this."""
    for repo_path in resolved_frame_paths(case_id):
        assert (FIXTURE_REPO / repo_path).is_file(), f"{case_id}: {repo_path} is not a file"


def test_the_case_that_needs_the_tree_is_still_honest() -> None:
    """`config-02` reports `/workspace/services/services/export.py`. S4 strips
    the documented prefix, gets a path that is not a file, and cannot know —
    it has no repo access. What it must not do is claim the configured
    confidence for a heuristic guess."""
    frame = understanding_for("config-02").in_app_frames[0]
    assert frame.repo_path == "services/services/export.py"
    assert not (FIXTURE_REPO / frame.repo_path).exists()
    assert frame.confidence == 0.80


# ── Criterion 3: exception family correct for ≥ 23/25 ──────────────────────


def test_exception_family_is_correct_for_at_least_23_of_25() -> None:
    wrong = sorted(
        case_id
        for case_id in CASE_IDS
        if understanding_for(case_id).exception.family
        != case(case_id)["expected"]["exception_family"]
    )
    correct = len(CASE_IDS) - len(wrong)
    assert correct >= MIN_FAMILIES, f"{correct}/25; missed {wrong}"
    assert wrong == sorted(NEEDS_BREADCRUMB_READING), f"unexpected misclassifications: {wrong}"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_no_case_is_left_unclassified(case_id: str) -> None:
    """`unclassified` is an honest answer and a bad one. Every error in the
    corpus is an ordinary application failure; if one of them cannot be placed,
    the taxonomy has a hole rather than the corpus having an oddity."""
    assert understanding_for(case_id).exception.family is not ExceptionFamily.UNCLASSIFIED


# ── The reference case, whose values `18` §7 pins ──────────────────────────


def test_the_reference_case_matches_the_specification_example() -> None:
    """`03` §S4's worked example and `18` §7's canonical values, together."""
    understanding = understanding_for("null-prop-01")

    assert understanding.exception.family is ExceptionFamily.NULL_UNDEFINED
    assert understanding.exception.message_normalized == (
        "unsupported operand type(s) for +: '<type>' and '<type>'"
    )
    assert understanding.exception.is_user_facing is True

    assert understanding.failure_point is not None
    assert understanding.failure_point.repo_path == "services/checkout.py"
    assert understanding.failure_point.function == "calculate_total"
    assert understanding.failure_point.line == 142

    assert understanding.entry_point is not None
    assert understanding.entry_point.pattern == "/api/v2/checkout"
    assert understanding.entry_point.method == "POST"

    assert understanding.implicated_symbols == (
        "calculate_total",
        "base_price",
        "tax_amount",
        "_build_response",
    )
    assert understanding.retrieval_plan.must_fetch == (
        "services/checkout.py",
        "api/routes/checkout.py",
    )

    # `18` §7 pins the breadcrumb at T-141 ms and calls it the decisive
    # evidence. It is a `warning` among `info`, which is what selects it.
    assert understanding.retrieval_plan.breadcrumb_signal is not None
    assert "141 ms before the error" in understanding.retrieval_plan.breadcrumb_signal
    assert "tax-service" in understanding.retrieval_plan.breadcrumb_signal


def test_the_root_cause_file_is_reachable_only_by_expansion() -> None:
    """The measurement that decides whether Phase 7 works.

    `null-prop-01`'s root cause is `clients/tax_client.py`, and it appears in
    no frame, no breadcrumb and nowhere in the message. **No plan built from
    this payload can name it** — S5's call-graph expansion (strategy B, T4.3)
    is what has to close the gap, and this test exists so that fact is
    recorded here rather than discovered as a retrieval failure later.
    """
    understanding = understanding_for("null-prop-01")
    root_cause = case("null-prop-01")["expected"]["root_cause_file"]

    assert root_cause == "clients/tax_client.py"
    assert root_cause not in understanding.retrieval_plan.must_fetch

    # What S4 *can* do is say which value was null and ask for its producer.
    assert "tax_amount" in understanding.implicated_symbols
    assert any("tax_amount" in query for query in understanding.retrieval_plan.semantic_queries)


# ── Controls ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case_id", ["unfixable-01", "unfixable-02"])
def test_the_controls_are_understood_like_any_other_error(case_id: str) -> None:
    """S4 has no opinion about fixability — `insufficient_context` is S5's
    verdict and a fabricated root cause is S6's failure. The controls must
    reach S5 looking exactly like any other integration error, or they stop
    testing what `18` §7 says they test."""
    understanding = understanding_for(case_id)
    assert understanding.exception.family is ExceptionFamily.INTEGRATION
    assert understanding.retrieval_plan.must_fetch


def test_the_health_check_control_is_not_user_facing() -> None:
    """`unfixable-02` fails on `/health/ready`."""
    assert understanding_for("unfixable-02").exception.is_user_facing is False
    assert understanding_for("unfixable-01").exception.is_user_facing is True
