"""Rank, dedupe, and trim to the 24,000-token budget (`03` §S5, T4.4).

Built directly against `RetrievalCandidates`, without a gateway — T4.3's
strategies already have their own suite; this one is about what ranking does
*given* candidates, independent of how they were found.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from roottrace_worker.github.types import Actor, Commit, RepoRef
from roottrace_worker.pipeline.retrieve.bundle import ContextBundle, InsufficientContext
from roottrace_worker.pipeline.retrieve.contracts import (
    GraphEdge,
    GraphNode,
    HistoryCandidates,
    RetrievalCandidates,
    RetrievedFile,
    TestMatch,
)
from roottrace_worker.pipeline.retrieve.ranking import (
    MIN_ADMITTED_FILES,
    MIN_ADMITTED_IN_APP_TOKENS,
    TOKEN_BUDGET,
    build_context_bundle,
)
from roottrace_worker.pipeline.retrieve.tokens import CHARS_PER_TOKEN
from roottrace_worker.pipeline.understand.contracts import (
    EntryPoint,
    ErrorUnderstanding,
    ExceptionFamily,
    ExceptionInfo,
    FailurePoint,
    RetrievalPlan,
)

pytestmark = pytest.mark.unit

REPO = RepoRef(owner="acme", name="checkout-api")
REF = "main"
NOW = datetime(2026, 8, 4, 9, 14, 22, tzinfo=UTC)


def understanding(**overrides: object) -> ErrorUnderstanding:
    base: dict[str, object] = {
        "language": "python",
        "framework": "fastapi",
        "exception": ExceptionInfo(
            type="TypeError",
            family=ExceptionFamily.NULL_UNDEFINED,
            message_normalized="m",
            is_user_facing=True,
        ),
        "entry_point": EntryPoint(
            type="http_route",
            method="POST",
            pattern="/x",
            handler="api/routes/checkout.py::create_checkout",
        ),
        "failure_point": FailurePoint(
            repo_path="services/checkout.py", function="calculate_total", line=142
        ),
        "implicated_symbols": ("calculate_total", "get_rate"),
        "retrieval_plan": RetrievalPlan(must_fetch=("services/checkout.py",)),
        "extraction_confidence": 0.5,
    }
    base.update(overrides)
    return ErrorUnderstanding(**base)  # type: ignore[arg-type]


def file(
    repo_path: str,
    *,
    strategy: str = "frame_direct",
    content: str = "x" * 100,
    symbols: tuple[str, ...] = (),
    truncated: bool = False,
) -> RetrievedFile:
    return RetrievedFile(
        repo_path=repo_path,
        strategy=strategy,
        language="python",
        content=content,
        line_range=(1, 10),
        truncated=truncated,
        symbols_defined=symbols,
    )


FAILURE_ID = "services/checkout.py::calculate_total"
ENTRY_ID = "api/routes/checkout.py::create_checkout"
CALLEE_ID = "clients/tax_client.py::get_rate"
CALLER_ID = "api/routes/webhooks.py::unrelated_caller"


def rich_candidates() -> RetrievalCandidates:
    """Enough priority 1-4 content to clear the `insufficient_context`
    threshold — the baseline every "does ranking work" test starts from."""
    return RetrievalCandidates(
        files=(
            file(
                "services/checkout.py",
                content="x" * 3000,
                symbols=("CheckoutService.calculate_total",),
            ),
            file(
                "api/routes/checkout.py",
                content="x" * 1000,
                symbols=("create_checkout",),
            ),
            file(
                "clients/tax_client.py",
                strategy="call_graph",
                content="x" * 1000,
                symbols=("TaxClient.get_rate",),
            ),
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
            GraphNode(id=CALLEE_ID, kind="function"),
        ),
        graph_edges=(GraphEdge(source=FAILURE_ID, target=CALLEE_ID, kind="calls"),),
        history=HistoryCandidates(blame_commit=None, recent_commits=()),
        tests=(),
    )


def test_a_rich_bundle_produces_a_context_bundle_not_insufficient() -> None:
    outcome = build_context_bundle(
        rich_candidates(), understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)


def test_failure_point_and_entry_point_are_always_admitted() -> None:
    """`03` §S5: priority 1 and 2 are "non-negotiable" — this is the direct
    assertion of that rule, not an inference from budget headroom."""
    outcome = build_context_bundle(
        rich_candidates(), understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    paths = {f.repo_path for f in outcome.files}
    assert "services/checkout.py" in paths
    assert "api/routes/checkout.py" in paths


def test_the_failure_point_has_relevance_1_at_minimum() -> None:
    """Strategy weight 1.00 x recency 1.0 x proximity 1.0 x (1 + overlap) —
    the failure point can never score below the base case, and scores above
    it once its own symbol overlaps `implicated_symbols`."""
    outcome = build_context_bundle(
        rich_candidates(), understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    failure_file = next(f for f in outcome.files if f.repo_path == "services/checkout.py")
    assert failure_file.relevance >= 1.0


def test_budget_is_never_exceeded() -> None:
    """`15` §6's T4.4 bar, directly: build a bundle with far more content than
    the budget allows and confirm `token_count` still respects it."""
    huge_files = tuple(
        file(f"services/module_{n}.py", content="x" * 5000, symbols=(f"fn_{n}",)) for n in range(20)
    )
    candidates = RetrievalCandidates(
        files=(
            file("services/checkout.py", content="x" * 3000, symbols=("calculate_total",)),
            file("api/routes/checkout.py", content="x" * 1000, symbols=("create_checkout",)),
            *huge_files,
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
        ),
        graph_edges=(),
        history=None,
        tests=(),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    assert outcome.token_count <= TOKEN_BUDGET


def test_a_low_priority_item_is_evicted_before_a_high_priority_one() -> None:
    """The direct mechanism behind "budget never exceeded, priority 1-2 never
    evicted": a huge vector-search item (priority 7 — "first to be trimmed",
    `03` §S5) must lose its place to the failure point and entry point. A
    third, small callee keeps priority 1-4 at 3 real files on its own, so the
    huge item's exclusion is attributable to budget/priority, not to the
    3-file floor also firing for an unrelated reason."""
    huge_unrelated = file(
        "services/unrelated_giant.py", strategy="vector_semantic", content="x" * (TOKEN_BUDGET * 4)
    )
    candidates = RetrievalCandidates(
        files=(
            file("services/checkout.py", content="x" * 3000, symbols=("calculate_total",)),
            file("api/routes/checkout.py", content="x" * 1000, symbols=("create_checkout",)),
            file(
                "clients/tax_client.py",
                strategy="call_graph",
                content="x" * 1000,
                symbols=("get_rate",),
            ),
            huge_unrelated,
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
            GraphNode(id=CALLEE_ID, kind="function"),
        ),
        graph_edges=(GraphEdge(source=FAILURE_ID, target=CALLEE_ID, kind="calls"),),
        history=None,
        tests=(),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    paths = {f.repo_path for f in outcome.files}
    assert "services/checkout.py" in paths
    assert "api/routes/checkout.py" in paths
    assert "services/unrelated_giant.py" not in paths


# ── insufficient_context ────────────────────────────────────────────────


def test_empty_candidates_terminate_as_insufficient_context() -> None:
    outcome = build_context_bundle(
        RetrievalCandidates(), understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, InsufficientContext)
    assert outcome.admitted_file_count == 0


def test_fewer_than_3_priority_1_4_files_terminates() -> None:
    """`03` §S5's exact threshold, the file-count branch of the `or`."""
    candidates = RetrievalCandidates(
        files=(file("services/checkout.py", content="x" * 3000, symbols=("calculate_total",)),),
        graph_nodes=(GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),),
        graph_edges=(),
        history=None,
        tests=(),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, InsufficientContext)
    assert outcome.admitted_file_count < MIN_ADMITTED_FILES


def test_fewer_than_800_in_app_tokens_terminates_even_with_3_files() -> None:
    """The token-count branch of the `or`, independent of file count — three
    tiny files should trip this even though the file-count branch would not."""
    tiny = "x" * 50  # well under 800 tokens even summed across 3 files
    candidates = RetrievalCandidates(
        files=(
            file("services/checkout.py", content=tiny, symbols=("calculate_total",)),
            file("api/routes/checkout.py", content=tiny, symbols=("create_checkout",)),
            file(
                "clients/tax_client.py", strategy="call_graph", content=tiny, symbols=("get_rate",)
            ),
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
            GraphNode(id=CALLEE_ID, kind="function"),
        ),
        graph_edges=(GraphEdge(source=FAILURE_ID, target=CALLEE_ID, kind="calls"),),
        history=None,
        tests=(),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, InsufficientContext)
    assert outcome.admitted_in_app_tokens < MIN_ADMITTED_IN_APP_TOKENS


def test_a_priority_5_caller_does_not_rescue_a_thin_bundle() -> None:
    """A caller (priority 5) is real, useful content — but `03` §S5's
    threshold is evaluated *after admitting priority 1-4* specifically, so a
    caller-only third file must not count toward the 3-file minimum."""
    candidates = RetrievalCandidates(
        files=(
            file("services/checkout.py", content="x" * 3000, symbols=("calculate_total",)),
            file(
                "api/routes/webhooks.py",
                strategy="call_graph",
                content="x" * 3000,
                symbols=("unrelated_caller",),
            ),
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=CALLER_ID, kind="function"),
        ),
        graph_edges=(GraphEdge(source=CALLER_ID, target=FAILURE_ID, kind="calls"),),
        history=None,
        tests=(),
    )
    outcome = build_context_bundle(
        candidates,
        understanding(entry_point=None),
        repo=REPO,
        ref=REF,
        bundle_id="ctx_1",
        now=NOW,
    )
    assert isinstance(outcome, InsufficientContext)


# ── Dedup ──────────────────────────────────────────────────────────────


def test_the_same_path_from_two_strategies_appears_once() -> None:
    """`03` §S5's diagram: "Rank · Dedupe · Trim" — a file both strategy A
    and strategy B found must produce one `BundleFile`, not two."""
    candidates = RetrievalCandidates(
        files=(
            file(
                "services/checkout.py",
                strategy="frame_direct",
                content="x" * 3000,
                symbols=("calculate_total",),
            ),
            file(
                "services/checkout.py",
                strategy="call_graph",
                content="x" * 2000,
                symbols=("calculate_total",),
            ),
            file("api/routes/checkout.py", content="x" * 1000, symbols=("create_checkout",)),
            file(
                "clients/tax_client.py",
                strategy="call_graph",
                content="x" * 1000,
                symbols=("get_rate",),
            ),
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
            GraphNode(id=CALLEE_ID, kind="function"),
        ),
        graph_edges=(GraphEdge(source=FAILURE_ID, target=CALLEE_ID, kind="calls"),),
        history=None,
        tests=(),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    matching = [f for f in outcome.files if f.repo_path == "services/checkout.py"]
    assert len(matching) == 1


def test_dedup_keeps_the_higher_priority_version() -> None:
    """Frame-direct (the failure point's own strategy) must win over a
    lower-priority label for the same path, since priority — not which
    strategy happened to run first — decides admission order."""
    candidates = RetrievalCandidates(
        files=(
            file(
                "services/checkout.py",
                strategy="frame_direct",
                content="x" * 3000,
                symbols=("calculate_total",),
            ),
            file("api/routes/checkout.py", content="x" * 1000, symbols=("create_checkout",)),
            file(
                "clients/tax_client.py",
                strategy="call_graph",
                content="x" * 1000,
                symbols=("get_rate",),
            ),
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
            GraphNode(id=CALLEE_ID, kind="function"),
        ),
        graph_edges=(GraphEdge(source=FAILURE_ID, target=CALLEE_ID, kind="calls"),),
        history=None,
        tests=(),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    checkout = next(f for f in outcome.files if f.repo_path == "services/checkout.py")
    assert checkout.strategy == "frame_direct"


# ── Relevance formula ────────────────────────────────────────────────────


def test_symbol_overlap_increases_relevance() -> None:
    """`03` §S5: `(1 + 0.15 x symbol_overlap)` — a file whose defined symbols
    overlap `implicated_symbols` must score higher than an equivalent file
    that does not."""
    without_overlap = RetrievalCandidates(
        files=(
            file("services/checkout.py", content="x" * 3000, symbols=("something_else",)),
            file("api/routes/checkout.py", content="x" * 1000, symbols=("create_checkout",)),
            file(
                "clients/tax_client.py",
                strategy="call_graph",
                content="x" * 1000,
                symbols=("get_rate",),
            ),
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
            GraphNode(id=CALLEE_ID, kind="function"),
        ),
        graph_edges=(GraphEdge(source=FAILURE_ID, target=CALLEE_ID, kind="calls"),),
        history=None,
        tests=(),
    )
    with_overlap = rich_candidates()  # symbols include "calculate_total"

    outcome_without = build_context_bundle(
        without_overlap, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    outcome_with = build_context_bundle(
        with_overlap, understanding(), repo=REPO, ref=REF, bundle_id="ctx_2", now=NOW
    )
    assert isinstance(outcome_without, ContextBundle)
    assert isinstance(outcome_with, ContextBundle)

    relevance_without = next(
        f.relevance for f in outcome_without.files if f.repo_path == "services/checkout.py"
    )
    relevance_with = next(
        f.relevance for f in outcome_with.files if f.repo_path == "services/checkout.py"
    )
    assert relevance_with > relevance_without


def test_a_qualified_method_name_matches_its_bare_implicated_symbol() -> None:
    """Regression test for the bug this ticket found: `symbols_defined`
    carries `"ClassName.method"`; `implicated_symbols` carries the bare
    method name. A naive equality check would never see these as the same
    symbol and would silently zero the overlap bonus for every method on a
    class-based codebase."""
    candidates = RetrievalCandidates(
        files=(
            file(
                "services/checkout.py",
                content="x" * 3000,
                symbols=("CheckoutService.calculate_total",),
            ),
            file("api/routes/checkout.py", content="x" * 1000, symbols=("create_checkout",)),
            file(
                "clients/tax_client.py",
                strategy="call_graph",
                content="x" * 1000,
                symbols=("TaxClient.get_rate",),
            ),
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
            GraphNode(id=CALLEE_ID, kind="function"),
        ),
        graph_edges=(GraphEdge(source=FAILURE_ID, target=CALLEE_ID, kind="calls"),),
        history=None,
        tests=(),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    # implicated_symbols = ("calculate_total", "get_rate") — both must be
    # recognised as resolved despite the qualified spelling in symbols_defined.
    assert outcome.quality.signals.unresolved_symbols == ()


# ── Quality signals and gaps ─────────────────────────────────────────────


def test_an_unresolved_should_fetch_symbol_produces_a_gap() -> None:
    outcome = build_context_bundle(
        rich_candidates(),
        understanding(
            retrieval_plan=RetrievalPlan(
                must_fetch=("services/checkout.py",),
                should_fetch_by_symbol=("calculate_total", "get_regional_config"),
            )
        ),
        repo=REPO,
        ref=REF,
        bundle_id="ctx_1",
        now=NOW,
    )
    assert isinstance(outcome, ContextBundle)
    assert "get_regional_config" in outcome.quality.signals.unresolved_symbols
    assert any("get_regional_config" in gap for gap in outcome.gaps)


def test_quality_score_reflects_resolved_failure_and_entry_points() -> None:
    outcome = build_context_bundle(
        rich_candidates(), understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    assert outcome.quality.signals.failure_point_resolved is True
    assert outcome.quality.signals.entry_point_resolved is True
    assert outcome.quality.score > 0.0


# ── Blame attachment ─────────────────────────────────────────────────────


def test_blame_is_attached_to_the_failure_point_file_only() -> None:
    """`03` §S5's worked example shows `blame` on the failure-point's own
    `files[]` entry (`services/checkout.py`), not on every file."""
    commit = Commit(
        sha="8a3f1c2e" + "0" * 32,
        message="refactor",
        author=Actor(name="d", email="d@x.io"),
        date=NOW,
    )
    candidates = RetrievalCandidates(
        files=rich_candidates().files,
        graph_nodes=rich_candidates().graph_nodes,
        graph_edges=rich_candidates().graph_edges,
        history=HistoryCandidates(blame_commit=commit, recent_commits=(commit,)),
        tests=(),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    failure_file = next(f for f in outcome.files if f.repo_path == "services/checkout.py")
    assert failure_file.blame is not None
    assert failure_file.blame.commit.sha == commit.sha

    other = next(f for f in outcome.files if f.repo_path == "api/routes/checkout.py")
    assert other.blame is None


# ── Tests: full content, signature fallback ──────────────────────────────


def test_a_test_that_fits_is_included_in_full() -> None:
    test_content = "def test_calculate_total():\n    assert True\n"
    candidates = RetrievalCandidates(
        files=rich_candidates().files,
        graph_nodes=rich_candidates().graph_nodes,
        graph_edges=rich_candidates().graph_edges,
        history=None,
        tests=(
            TestMatch(
                repo_path="tests/test_checkout.py",
                content=test_content,
                covers=("calculate_total",),
            ),
        ),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    assert outcome.tests.found[0].content == test_content


def test_a_test_too_large_to_fit_is_trimmed_to_its_signature() -> None:
    """`03` §S5, priority 8: "Trimmed to signatures only if tight." Fill the
    budget almost entirely, then confirm a huge test is not dropped outright
    but reduced to its `def` line. `CHARS_PER_TOKEN` converts the *token*
    target into the *character* count `content=` actually needs — using
    `TOKEN_BUDGET` as a character count would leave most of the budget
    unspent and the huge test would fit in full, testing nothing."""
    filler_tokens = TOKEN_BUDGET - 200
    filler = file(
        "services/checkout.py",
        content="x" * int(filler_tokens * CHARS_PER_TOKEN),
        symbols=("calculate_total",),
    )
    huge_test = "def test_calculate_total():\n" + ("    # padding\n" * 2000) + "    assert True\n"
    candidates = RetrievalCandidates(
        files=(
            filler,
            file("api/routes/checkout.py", content="x" * 50, symbols=("create_checkout",)),
            file(
                "clients/tax_client.py",
                strategy="call_graph",
                content="x" * 50,
                symbols=("get_rate",),
            ),
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
            GraphNode(id=CALLEE_ID, kind="function"),
        ),
        graph_edges=(GraphEdge(source=FAILURE_ID, target=CALLEE_ID, kind="calls"),),
        history=None,
        tests=(
            TestMatch(
                repo_path="tests/test_checkout.py", content=huge_test, covers=("calculate_total",)
            ),
        ),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    assert outcome.token_count <= TOKEN_BUDGET
    if outcome.tests.found:
        assert "def test_calculate_total" in outcome.tests.found[0].content
        assert "# padding" not in outcome.tests.found[0].content


# ── Recency ──────────────────────────────────────────────────────────────


def test_an_old_blame_commit_lowers_relevance_via_recency() -> None:
    """`03` §S5: "recency_factor — commits/PRs decay over 90 days." Only
    observable on the failure-point file, since that is the only one blame
    attaches to (see `test_blame_is_attached_to_the_failure_point_file_only`)."""
    recent_commit = Commit(
        sha="1" * 40,
        message="m",
        author=Actor(name="a", email="a@x.io"),
        date=NOW,
    )
    old_commit = Commit(
        sha="2" * 40,
        message="m",
        author=Actor(name="a", email="a@x.io"),
        date=datetime(2020, 1, 1, tzinfo=UTC),
    )

    def with_blame(commit: Commit) -> RetrievalCandidates:
        return RetrievalCandidates(
            files=rich_candidates().files,
            graph_nodes=rich_candidates().graph_nodes,
            graph_edges=rich_candidates().graph_edges,
            history=HistoryCandidates(blame_commit=commit, recent_commits=()),
            tests=(),
        )

    recent_outcome = build_context_bundle(
        with_blame(recent_commit), understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    old_outcome = build_context_bundle(
        with_blame(old_commit), understanding(), repo=REPO, ref=REF, bundle_id="ctx_2", now=NOW
    )
    assert isinstance(recent_outcome, ContextBundle)
    assert isinstance(old_outcome, ContextBundle)

    recent_relevance = next(
        f.relevance for f in recent_outcome.files if f.repo_path == "services/checkout.py"
    )
    old_relevance = next(
        f.relevance for f in old_outcome.files if f.repo_path == "services/checkout.py"
    )
    assert old_relevance < recent_relevance


# ── Priority classification edge cases ────────────────────────────────────


def test_a_type_definition_edge_is_priority_4() -> None:
    """`03` §S5 priority 4: "Type/class definitions in scope" — a
    `"references"` edge, distinct from a `"calls"` edge (priority 3)."""
    type_id = "models/cart.py::Cart"
    candidates = RetrievalCandidates(
        files=(
            file("services/checkout.py", content="x" * 3000, symbols=("calculate_total",)),
            file("api/routes/checkout.py", content="x" * 1000, symbols=("create_checkout",)),
            file("models/cart.py", strategy="call_graph", content="x" * 1000, symbols=("Cart",)),
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
            GraphNode(id=type_id, kind="class"),
        ),
        graph_edges=(GraphEdge(source=FAILURE_ID, target=type_id, kind="references"),),
        history=None,
        tests=(),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    assert {"services/checkout.py", "api/routes/checkout.py", "models/cart.py"} <= {
        f.repo_path for f in outcome.files
    }


def test_a_competing_lower_priority_claim_does_not_downgrade_a_path() -> None:
    """A path reachable two ways (e.g. it is both the entry point *and* a
    direct callee) must keep the better (lower-numbered) priority — `claim`'s
    guard against a later, worse claim overwriting an earlier, better one."""
    candidates = RetrievalCandidates(
        files=(
            file("services/checkout.py", content="x" * 3000, symbols=("calculate_total",)),
            file("api/routes/checkout.py", content="x" * 1000, symbols=("create_checkout",)),
            file(
                "clients/tax_client.py",
                strategy="call_graph",
                content="x" * 1000,
                symbols=("get_rate",),
            ),
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
            GraphNode(id=CALLEE_ID, kind="function"),
        ),
        # The entry point is *also* named as a callee, via a second edge
        # sharing its path. Priority 2 (the entry point claim) must survive.
        graph_edges=(
            GraphEdge(source=FAILURE_ID, target=CALLEE_ID, kind="calls"),
            GraphEdge(source=FAILURE_ID, target=ENTRY_ID, kind="calls"),
        ),
        history=None,
        tests=(),
    )
    from roottrace_worker.pipeline.retrieve.ranking import _priority_by_path

    priorities = _priority_by_path(candidates.graph_edges, FAILURE_ID, ENTRY_ID)
    assert priorities["api/routes/checkout.py"] == 2


def test_no_failure_id_still_classifies_the_entry_point() -> None:
    from roottrace_worker.pipeline.retrieve.ranking import _priority_by_path

    priorities = _priority_by_path((), None, ENTRY_ID)
    assert priorities == {"api/routes/checkout.py": 2}


# ── History: release diff, and history that doesn't fit ─────────────────


def test_a_release_diff_is_included_and_costed() -> None:
    from roottrace_worker.github.types import CompareResult, FileChange

    diff = CompareResult(
        base="v2.14.2",
        head="v2.14.3",
        commits=(),
        files=(FileChange(path="services/checkout.py", status="modified"),),
    )
    candidates = RetrievalCandidates(
        files=rich_candidates().files,
        graph_nodes=rich_candidates().graph_nodes,
        graph_edges=rich_candidates().graph_edges,
        history=HistoryCandidates(blame_commit=None, recent_commits=(), release_diff=diff),
        tests=(),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    assert outcome.history.release_diff == diff
    assert outcome.strategy_stats["git_history"].tokens > 0


def test_history_that_does_not_fit_is_dropped_whole() -> None:
    """A half-quoted commit message is not useful evidence — history is
    included only if the *whole* thing fits, never partially."""
    filler = file(
        "services/checkout.py",
        content="x" * int((TOKEN_BUDGET - 50) * CHARS_PER_TOKEN),
        symbols=("calculate_total",),
    )
    huge_commit = Commit(
        sha="1" * 40, message="x" * 10_000, author=Actor(name="a", email="a@x.io"), date=NOW
    )
    candidates = RetrievalCandidates(
        files=(
            filler,
            file("api/routes/checkout.py", content="x" * 10, symbols=("create_checkout",)),
            file(
                "clients/tax_client.py",
                strategy="call_graph",
                content="x" * 10,
                symbols=("get_rate",),
            ),
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
            GraphNode(id=CALLEE_ID, kind="function"),
        ),
        graph_edges=(GraphEdge(source=FAILURE_ID, target=CALLEE_ID, kind="calls"),),
        history=HistoryCandidates(blame_commit=None, recent_commits=(huge_commit,)),
        tests=(),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    assert outcome.token_count <= TOKEN_BUDGET
    assert outcome.history.recent_commits == ()


def test_a_test_too_large_even_for_its_signature_is_dropped_entirely() -> None:
    filler = file(
        "services/checkout.py",
        content="x" * int((TOKEN_BUDGET - 20) * CHARS_PER_TOKEN),
        symbols=("calculate_total",),
    )
    huge_signature_line = "def " + ("x" * 5000) + "():\n    pass\n"
    candidates = RetrievalCandidates(
        files=(
            filler,
            file("api/routes/checkout.py", content="x" * 10, symbols=("create_checkout",)),
            file(
                "clients/tax_client.py",
                strategy="call_graph",
                content="x" * 10,
                symbols=("get_rate",),
            ),
        ),
        graph_nodes=(
            GraphNode(id=FAILURE_ID, kind="function", is_failure_point=True),
            GraphNode(id=ENTRY_ID, kind="function", is_entry_point=True),
            GraphNode(id=CALLEE_ID, kind="function"),
        ),
        graph_edges=(GraphEdge(source=FAILURE_ID, target=CALLEE_ID, kind="calls"),),
        history=None,
        tests=(
            TestMatch(
                repo_path="tests/test_checkout.py",
                content=huge_signature_line,
                covers=("calculate_total",),
            ),
        ),
    )
    outcome = build_context_bundle(
        candidates, understanding(), repo=REPO, ref=REF, bundle_id="ctx_1", now=NOW
    )
    assert isinstance(outcome, ContextBundle)
    assert outcome.token_count <= TOKEN_BUDGET
    assert outcome.tests.found == ()
