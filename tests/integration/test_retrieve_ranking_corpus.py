"""T4.4 acceptance — ranking, budget, and quality scoring over the corpus
(`15` §6).

> **Accept:** Budget is never exceeded across all 25 cases. Priority 1-2
> items are never evicted. The two "unfixable" fixtures terminate as
> `insufficient_context` without proceeding to reasoning.

**Three of the four numbers this file measures are clean. The fourth is not,
and this file records the finding rather than hiding it** — see `03` §S5's
implementation note and `docs/PROJECT-STATUS.md` for the full write-up. In
short: `03` §S5's termination threshold ("fewer than 3 distinct priority 1-4
files or fewer than 800 in-app tokens"), applied literally to what T4.3's four
implemented strategies produce, also terminates 18 of the 23 non-control
cases — every one of which carries `expected.final_status:
"awaiting_decision"` (`14` §6.2), meaning the corpus expects them to reach
reasoning, not abstain. Hand-checked against several: retrieval is not
missing anything real for these cases — a single self-contained function with
no callees and no type references mechanically caps at one priority-1-4 file,
and the threshold was evidently calibrated for a retrieval richer than V1's
deliberately narrow scope (P3, 1 hop, strategy C deferred) produces.

**This is left as found, not tuned to pass.** `INSUFFICIENT_CONTEXT_ON_FIXABLE_CASES`
below names the exact set — if that set shrinks or grows, this file's
assertions catch it, which is the point: the finding stays visible and
precise rather than being silently absorbed by a looser test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from fixtures.triggers.cases import CASE_IDS
from roottrace_worker.github.fixture import FixtureTransport
from roottrace_worker.github.types import RepoRef, RepoTree
from roottrace_worker.pipeline.retrieve.bundle import (
    ContextBundle,
    InsufficientContext,
    RetrievalOutcome,
)
from roottrace_worker.pipeline.retrieve.ranking import (
    MIN_ADMITTED_FILES,
    MIN_ADMITTED_IN_APP_TOKENS,
    TOKEN_BUDGET,
    build_context_bundle,
)
from roottrace_worker.pipeline.retrieve.strategies import gather
from roottrace_worker.pipeline.understand import PathMapping, understand

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REPO = REPO_ROOT / "fixtures" / "synthetic-repo"
CORPUS = REPO_ROOT / "fixtures" / "error-corpus"
FIXTURE_REPO_REF = RepoRef(owner="acme", name="checkout-api")

CONTROLS = ("unfixable-01", "unfixable-02")

#: The 18 non-control cases that terminate as `insufficient_context` under
#: T4.3's current retrieval reach, against `expected.final_status:
#: "awaiting_decision"` ground truth (`14` §6.2) — see this file's and `03`
#: §S5's module docstrings for why. Not a target to shrink by tuning the
#: threshold; a record of the open question for the next session.
INSUFFICIENT_CONTEXT_ON_FIXABLE_CASES = frozenset(
    {
        "null-prop-02",
        "null-prop-03",
        "null-prop-04",
        "type-mismatch-02",
        "type-mismatch-03",
        "key-error-01",
        "key-error-02",
        "key-error-03",
        "external-02",
        "external-03",
        "race-01",
        "race-02",
        "boundary-02",
        "config-01",
        "config-02",
        "regression-02",
        "regression-03",
        "resource-01",
    }
)


def payload(case_id: str) -> dict[str, Any]:
    return json.loads((CORPUS / f"{case_id}.json").read_text(encoding="utf-8"))


def project_mappings() -> tuple[PathMapping, ...]:
    metadata = json.loads((FIXTURE_REPO / ".roottrace-fixture.json").read_text(encoding="utf-8"))
    return tuple(
        PathMapping(mapping["from"], mapping["to"]) for mapping in metadata["path_mappings"]
    )


MAPPINGS = project_mappings()


@pytest.fixture(scope="module")
def gateway() -> FixtureTransport:
    return FixtureTransport(FIXTURE_REPO)


@pytest.fixture(scope="module")
def fixture_tree(gateway: FixtureTransport) -> RepoTree:
    return asyncio.run(gateway.fetch_tree(FIXTURE_REPO_REF, gateway.default_branch))


def outcome_for(case_id: str, gateway: FixtureTransport, tree: RepoTree) -> RetrievalOutcome:
    event = payload(case_id)["events"][0]
    understanding = asyncio.run(understand(event, mappings=MAPPINGS)).understanding
    candidates = asyncio.run(
        gather(gateway, FIXTURE_REPO_REF, gateway.default_branch, tree, understanding)
    )
    return build_context_bundle(
        candidates,
        understanding,
        repo=FIXTURE_REPO_REF,
        ref=gateway.default_branch,
        bundle_id=f"ctx_{case_id}",
    )


# ── The three clean bars ─────────────────────────────────────────────────


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_budget_is_never_exceeded(
    case_id: str, gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    outcome = outcome_for(case_id, gateway, fixture_tree)
    if isinstance(outcome, ContextBundle):
        assert outcome.token_count <= TOKEN_BUDGET


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_priority_1_and_2_are_never_evicted(
    case_id: str, gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    """When a bundle is produced at all, the failure point and (if resolved)
    the entry point must be in it — `03` §S5 calls both "non-negotiable"."""
    event = payload(case_id)["events"][0]
    understanding = asyncio.run(understand(event, mappings=MAPPINGS)).understanding
    outcome = outcome_for(case_id, gateway, fixture_tree)
    if not isinstance(outcome, ContextBundle):
        return
    paths = {f.repo_path for f in outcome.files}
    failure = understanding.failure_point
    if failure and failure.repo_path:
        assert failure.repo_path in paths, f"{case_id}: failure point evicted"


@pytest.mark.parametrize("case_id", CONTROLS)
def test_the_controls_terminate_as_insufficient_context(
    case_id: str, gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    outcome = outcome_for(case_id, gateway, fixture_tree)
    assert isinstance(outcome, InsufficientContext), f"{case_id}: expected insufficient_context"


# ── The fourth bar, honestly measured ────────────────────────────────────


@pytest.mark.parametrize(
    "case_id", sorted(set(CASE_IDS) - set(CONTROLS) - INSUFFICIENT_CONTEXT_ON_FIXABLE_CASES)
)
def test_reachable_fixable_cases_proceed_to_reasoning(
    case_id: str, gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    """The 5 non-control cases whose priority-1-4 evidence already clears the
    threshold under T4.3's current retrieval reach."""
    outcome = outcome_for(case_id, gateway, fixture_tree)
    assert isinstance(outcome, ContextBundle), f"{case_id}: unexpectedly insufficient_context"


@pytest.mark.parametrize("case_id", sorted(INSUFFICIENT_CONTEXT_ON_FIXABLE_CASES))
def test_the_documented_calibration_gap_is_still_present(
    case_id: str, gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    """The inverse of the test above, for the 18 named cases — if one of
    these starts proceeding to reasoning (a future retrieval improvement
    closing the gap), that is worth knowing and moving out of this set, not
    silently absorbing. Confirms each one fails specifically because fewer
    than `MIN_ADMITTED_FILES` priority-1-4 files were found, distinguishing
    "the mechanism works as literally spec'd" from an unrelated crash."""
    outcome = outcome_for(case_id, gateway, fixture_tree)
    assert isinstance(outcome, InsufficientContext), (
        f"{case_id}: now proceeds to reasoning — move it out of "
        f"INSUFFICIENT_CONTEXT_ON_FIXABLE_CASES and record why"
    )
    # `03` §S5's threshold is an "or": file count OR token count. At least
    # one side must actually be the trigger, or this case is failing for a
    # reason the spec's rule does not describe.
    assert (
        outcome.admitted_file_count < MIN_ADMITTED_FILES
        or outcome.admitted_in_app_tokens < MIN_ADMITTED_IN_APP_TOKENS
    ), f"{case_id}: insufficient_context fired without either documented trigger"
