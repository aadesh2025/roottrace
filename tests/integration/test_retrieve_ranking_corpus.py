"""T4.4 acceptance — ranking, budget, and quality scoring over the corpus
(`15` §6).

> **Accept:** Budget is never exceeded across all 25 cases. Priority 1-2
> items are never evicted. The two "unfixable" fixtures terminate as
> `insufficient_context` without proceeding to reasoning.

**The third bar, as originally written, assumed S5 alone could tell a
correctly-handled external failure apart from a genuine bug by evidence
volume.** Measuring the corpus disproved that: `external-03` (a real bug —
`InventoryClient.reserve` has no circuit breaker) and `unfixable-01` (a
control — `InventoryClient.reserve` already raises correctly, no defect
exists) admit the *identical* 2 files and 1231 tokens of priority 1-4
evidence. `config-01` (also a real bug) admits only 251 tokens — fewer than
either control. No file-count or token-count threshold separates the
fixable set from the controls; every number that admits `config-01` also
admits both controls, and every number that rejects the controls also
rejects `config-01`. See `03` §S5's implementation note and
`docs/PROJECT-STATUS.md` for the full write-up and the coordinator's
decision.

**The resolution, decided by the coordinator, not tuned in unilaterally:**
judging *fixability* from evidence volume was never S5's job — `03` already
gives S6 its own `insufficient_context` exit ("on evidence-binding failure
... terminal `insufficient_context`"), which is where a model concluding "no
defect, external cause" belongs. S5's threshold (`MIN_ADMITTED_FILES`,
`MIN_ADMITTED_IN_APP_TOKENS` in `ranking.py`) is lowered to what S5 can
honestly judge: did retrieval resolve the failure point with any real
in-app content, or not. Under that bar, all 25 corpus cases — the 23
fixable cases and both controls — correctly produce a `ContextBundle`: S5's
job is retrieval, and retrieval genuinely succeeded for all 25. Whether a
case is *fixable* is not yet decided for any of them, including the
controls, because S6 (reasoning) is not built. That determination, and the
corpus's `expected.final_status: "insufficient_context"` for the two
controls specifically, becomes a Phase 8 acceptance property, not T4.4's.
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
from roottrace_worker.pipeline.retrieve.bundle import ContextBundle, RetrievalOutcome
from roottrace_worker.pipeline.retrieve.ranking import (
    TOKEN_BUDGET,
    build_context_bundle,
)
from roottrace_worker.pipeline.retrieve.strategies import _resolve_failure_path, gather
from roottrace_worker.pipeline.understand import PathMapping, understand

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REPO = REPO_ROOT / "fixtures" / "synthetic-repo"
CORPUS = REPO_ROOT / "fixtures" / "error-corpus"
FIXTURE_REPO_REF = RepoRef(owner="acme", name="checkout-api")

CONTROLS = ("unfixable-01", "unfixable-02")


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


# ── The two bars S5 alone can honestly measure ───────────────────────────


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
    the entry point must be in it — `03` §S5 calls both "non-negotiable".

    Checked against the tree-verified path (`_resolve_failure_path`), not
    `understanding.failure_point.repo_path` directly — that field is S4's
    cascade steps 1-2 alone (`03` §S4 has no repo access) and can be a
    well-formed path that isn't a real file (`config-02`'s
    `services/services/export.py`); T4.2/T4.3 re-verify it against the tree
    before ever admitting a file, so this check must too."""
    event = payload(case_id)["events"][0]
    understanding = asyncio.run(understand(event, mappings=MAPPINGS)).understanding
    outcome = outcome_for(case_id, gateway, fixture_tree)
    if not isinstance(outcome, ContextBundle):
        return
    paths = {f.repo_path for f in outcome.files}
    failure = understanding.failure_point
    if failure and failure.repo_path:
        resolved = _resolve_failure_path(understanding, fixture_tree, failure)
        if resolved:
            assert resolved in paths, f"{case_id}: failure point evicted"


# ── Retrieval succeeds for every case, controls included ────────────────


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_case_reaches_a_real_context_bundle(
    case_id: str, gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    """All 25 cases, including both controls, resolve real evidence at S5 —
    the controls' `InventoryClient` code is genuinely retrievable, it simply
    contains no defect. Distinguishing "retrieved, and fixable" from
    "retrieved, and not fixable" is S6's job (`03` line 751's
    `insufficient_context` exit on evidence-binding failure), not S5's."""
    outcome = outcome_for(case_id, gateway, fixture_tree)
    assert isinstance(outcome, ContextBundle), (
        f"{case_id}: S5 could not resolve any real evidence for the failure "
        "point — this would be a genuine retrieval failure, not a fixability "
        "judgment"
    )


@pytest.mark.parametrize("case_id", CONTROLS)
def test_the_controls_retrieve_the_client_boundary_correctly(
    case_id: str, gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    """Not a fixability check (that's S6, not built) — a check that S5 found
    the right code for the right reason, so the eventual S6 judgment has
    real evidence to work from rather than an accidental thin bundle."""
    outcome = outcome_for(case_id, gateway, fixture_tree)
    assert isinstance(outcome, ContextBundle)
    paths = {f.repo_path for f in outcome.files}
    assert "clients/inventory_client.py" in paths, (
        f"{case_id}: expected the InventoryClient boundary in the bundle"
    )
