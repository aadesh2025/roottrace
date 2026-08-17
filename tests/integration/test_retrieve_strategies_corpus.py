"""T4.3 acceptance — retrieval strategies A, B, D, E over the corpus (`15` §6).

> **Accept:** For the running example, retrieval returns `checkout.py`,
> `tax_client.py`, `routes/checkout.py`, and `test_checkout.py`, plus the
> introducing commit `8a3f1c2`.

The running example is `null-prop-01`, and `18` §7 pins its canonical values.
`clients/tax_client.py` is the file this whole phase exists to prove: it
appears in no stack frame, no breadcrumb, nowhere in the message, and no plan
or path resolver built through T4.2 can name it — only call-graph expansion
(strategy B), walking one hop from `calculate_total`'s callees, reaches it.

Also runs strategies A, B, D, E across the full 25-case corpus and asserts
every retrieved path is real — the same "cannot drift" property T4.1's and
T4.2's corpus tests hold, extended to call-graph expansion, blame, and test
discovery. Uses the real `FixtureTransport` against `fixtures/synthetic-repo`.
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
from roottrace_worker.pipeline.retrieve.contracts import RetrievalCandidates
from roottrace_worker.pipeline.retrieve.strategies import gather, strategy_e_test_discovery
from roottrace_worker.pipeline.understand import PathMapping, understand
from roottrace_worker.pipeline.understand.contracts import ErrorUnderstanding

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REPO = REPO_ROOT / "fixtures" / "synthetic-repo"
CORPUS = REPO_ROOT / "fixtures" / "error-corpus"
FIXTURE_REPO_REF = RepoRef(owner="acme", name="checkout-api")

#: Controls have no root cause, no callable failure-point body worth
#: expanding — call-graph expansion over them is not what `18` §7's M14/M15
#: measure, and this file is about retrieval reach, not abstention.
CONTROLS = ("unfixable-01", "unfixable-02")


def case(case_id: str) -> dict[str, Any]:
    return json.loads((CORPUS / f"{case_id}.case.json").read_text(encoding="utf-8"))


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


def understanding_for(case_id: str) -> ErrorUnderstanding:
    event = payload(case_id)["events"][0]
    return asyncio.run(understand(event, mappings=MAPPINGS)).understanding


def candidates_for(case_id: str, gateway: FixtureTransport, tree: RepoTree) -> RetrievalCandidates:
    understanding = understanding_for(case_id)
    return asyncio.run(
        gather(gateway, FIXTURE_REPO_REF, gateway.default_branch, tree, understanding)
    )


# ── The running example (`03` §S5's worked example, `18` §7's canonical case) ──


def test_the_running_example_retrieves_all_four_files(
    gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    result = candidates_for("null-prop-01", gateway, fixture_tree)
    paths = {f.repo_path for f in result.files}

    assert "services/checkout.py" in paths
    assert "api/routes/checkout.py" in paths
    assert "clients/tax_client.py" in paths
    assert any(t.repo_path == "tests/test_checkout.py" for t in result.tests)


def test_tax_client_is_reachable_only_through_call_graph_expansion(
    gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    """The file this whole phase exists to prove reachable. It is in no
    frame, no breadcrumb, nowhere in the message (`tests/integration/test_understand_corpus.py::test_the_root_cause_file_is_reachable_only_by_expansion`),
    and `resolve_frame_path` (T4.2) cannot name it either — only strategy B's
    one-hop callee expansion, from `calculate_total` to `get_rate`, reaches
    it."""
    result = candidates_for("null-prop-01", gateway, fixture_tree)
    tax_client = next(f for f in result.files if f.repo_path == "clients/tax_client.py")
    assert tax_client.strategy == "call_graph"

    edge = next(
        e
        for e in result.graph_edges
        if e.kind == "calls" and e.target == "clients/tax_client.py::get_rate"
    )
    assert edge.source == "services/checkout.py::calculate_total"


def test_the_introducing_commit_is_found(gateway: FixtureTransport, fixture_tree: RepoTree) -> None:
    """`18` §7's canonical `introduced_by_commit`: `8a3f1c2e`."""
    result = candidates_for("null-prop-01", gateway, fixture_tree)
    assert result.history is not None
    assert result.history.blame_commit is not None
    assert result.history.blame_commit.sha.startswith("8a3f1c2")


def test_the_test_discovery_covers_the_failure_point() -> None:
    """Confirms strategy E specifically, independent of strategy B also
    reaching the test file as a caller (both are expected; T4.4 dedups)."""
    understanding = understanding_for("null-prop-01")
    tree = asyncio.run(FixtureTransport(FIXTURE_REPO).fetch_tree(FIXTURE_REPO_REF, "main"))
    matches = asyncio.run(
        strategy_e_test_discovery(
            FixtureTransport(FIXTURE_REPO),
            FIXTURE_REPO_REF,
            "main",
            tree,
            must_fetch_paths=understanding.retrieval_plan.must_fetch,
            implicated_symbols=understanding.implicated_symbols,
        )
    )
    assert any(m.repo_path == "tests/test_checkout.py" for m in matches)


# ── Corpus-wide: every retrieved path is real ───────────────────────────


@pytest.mark.parametrize("case_id", [c for c in CASE_IDS if c not in CONTROLS])
def test_every_retrieved_file_exists_in_the_repository(
    case_id: str, gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    """The check that cannot drift — same principle as T4.1's and T4.2's
    corpus tests, extended past frame paths to call-graph expansion."""
    result = candidates_for(case_id, gateway, fixture_tree)
    for item in result.files:
        assert (FIXTURE_REPO / item.repo_path).is_file(), f"{case_id}: {item.repo_path}"


#: Three corpus cases whose root cause is not reachable by any of T4.3's four
#: strategies, for reasons specific to each — not build failures, and not
#: absorbed silently. `15` §6's T4.3 acceptance criterion names one worked
#: example (`null-prop-01`) and does not require every case to resolve; this
#: set exists so a case moving from "reachable" to "unreachable" — or a
#: fourth one appearing — is a build break rather than a quiet regression.
ROOT_CAUSE_UNREACHABLE_BY_T4_3 = {
    # `reconcile` (the failure point) calls `estimate_total`, which calls
    # `get_rate` — the root cause is two hops away. T4.3 does exactly the one
    # hop `03` §S5 specifies for V1 ("2 hops only if budget remains", and
    # there is no budget concept yet — that is T4.4's).
    "regression-02": "root cause is 2 hops from the failure point; T4.3 does 1",
    # `batch_size()` reads `self.settings.export_batch_size` — an attribute
    # access on a value constructed elsewhere (`load_settings()`, called at
    # composition-root time, not from anywhere in this call graph). There is
    # no call edge from the failure point to the producer at all; strategy B
    # walks calls, not "where did this constructor argument come from".
    "config-02": "root cause is the producer of an injected value, reached by no call edge",
    # `total_with_rate` (the failure point) and `merge_price_book` (where the
    # defect is) are unrelated sibling functions in the same file, connected
    # only by both touching a shared `TypedRegistry` — never by a call from
    # one to the other. No hop count closes a gap with no edge to walk.
    "type-mismatch-03": "root cause reaches the failure point only through shared data, never a call",
}


@pytest.mark.parametrize(
    "case_id",
    [c for c in CASE_IDS if c not in CONTROLS and c not in ROOT_CAUSE_UNREACHABLE_BY_T4_3],
)
def test_every_reachable_case_retrieves_its_own_root_cause_file(
    case_id: str, gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    """`expected.root_cause_file` (`14` §6.2) is the file S6 must eventually
    cite. For every case not named in `ROOT_CAUSE_UNREACHABLE_BY_T4_3`,
    retrieval must fetch it — if it never does, no amount of reasoning
    downstream can recover, and that is retrieval's own honesty check against
    ground truth, not S6's."""
    root_cause_file = case(case_id)["expected"]["root_cause_file"]
    result = candidates_for(case_id, gateway, fixture_tree)
    paths = {f.repo_path for f in result.files}
    assert root_cause_file in paths, f"{case_id}: missing {root_cause_file}; got {sorted(paths)}"


@pytest.mark.parametrize("case_id", sorted(ROOT_CAUSE_UNREACHABLE_BY_T4_3))
def test_the_documented_unreachable_cases_are_still_honestly_unreachable(
    case_id: str, gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    """The inverse of the test above, for the three named exceptions — if one
    of these *starts* resolving (a future strategy improvement reaching
    further than documented), that is worth knowing and updating the miss
    set for, not silently absorbing."""
    root_cause_file = case(case_id)["expected"]["root_cause_file"]
    result = candidates_for(case_id, gateway, fixture_tree)
    paths = {f.repo_path for f in result.files}
    assert root_cause_file not in paths, (
        f"{case_id}: {root_cause_file} is now reachable — "
        f"remove it from ROOT_CAUSE_UNREACHABLE_BY_T4_3 and record why"
    )


@pytest.mark.parametrize("case_id", [c for c in CASE_IDS if c not in CONTROLS])
def test_every_case_retrieves_at_least_one_test(
    case_id: str, gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    """Every non-control corpus case has a real test in the synthetic repo
    covering its failure path (`A1` §5). If discovery finds none, either the
    convention or symbol-grep path has a gap worth knowing about now, not at
    S8's gate G4/G6."""
    result = candidates_for(case_id, gateway, fixture_tree)
    assert result.tests, f"{case_id}: no test discovered"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_no_strategy_ever_raises(
    case_id: str, gateway: FixtureTransport, fixture_tree: RepoTree
) -> None:
    """Including the two controls — retrieval has no opinion about
    fixability (`15` T4.1's note on this), so it must run identically over
    them without error. `insufficient_context` is a judgement T4.4 makes from
    what came back, not a crash."""
    result = candidates_for(case_id, gateway, fixture_tree)
    assert result.history is not None
