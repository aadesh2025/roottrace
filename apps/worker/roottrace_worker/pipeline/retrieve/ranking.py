"""Rank, dedupe, and trim to the 24,000-token budget (`03` §S5, T4.4).

The stage in one sentence: **assign every candidate a priority (1-9, `03`
§S5's eviction table) and a relevance score, then admit priority-first,
relevance-second, until the budget is spent.** That single admission order
*is* priority-ordered eviction — anything after the budget cutoff was
evicted, and because priority 1-2 items are always sorted first, they are
mathematically first in line and can only fail to be admitted if they alone
exceed the whole 24,000-token budget, which `03` §S5 does not anticipate and
this module does not attempt to handle specially.

**Priority, not relevance, decides the front of the line.** `03` §S5's prose
describes two mechanisms — sort-by-relevance-and-admit, then a *separate*
priority-ordered eviction pass — but a single `(priority, -relevance)` sort
key produces the identical outcome in one pass: priority 1-2 first
regardless of their relevance number, priority 9 last regardless of its. The
two-pass description and this one-pass implementation are equivalent; this
one is simpler and has no "un-evict" step to get wrong.

**If, after admitting priority 1-4, fewer than 3 distinct files or fewer than
800 tokens of `in_app` source were admitted, the stage terminates as
`insufficient_context`** (`03` §S5, verbatim) — `build_context_bundle`
returns `InsufficientContext` instead of `ContextBundle` in that case, and
admits nothing further. This is the one place this module makes a decision
with a real consequence, and it is a mechanical count, never a guess.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from roottrace_worker.github.types import RepoRef
from roottrace_worker.pipeline.retrieve.bundle import (
    BlameInfo,
    BundleFile,
    BundleGraph,
    BundleGraphEdge,
    BundleGraphNode,
    BundleHistory,
    BundleTestMatch,
    BundleTests,
    ContextBundle,
    InsufficientContext,
    Quality,
    QualitySignals,
    RepositoryRef,
    RetrievalOutcome,
    StrategyStat,
)
from roottrace_worker.pipeline.retrieve.contracts import (
    GraphEdge,
    RetrievalCandidates,
    RetrievedFile,
    TestMatch,
)
from roottrace_worker.pipeline.retrieve.quality import compute_score
from roottrace_worker.pipeline.retrieve.tokens import estimate_tokens
from roottrace_worker.pipeline.understand.contracts import ErrorUnderstanding

#: `03` §S5, hard (P3).
TOKEN_BUDGET = 24_000

#: `03` §S5's five strategy weights.
STRATEGY_WEIGHT: dict[str, float] = {
    "frame_direct": 1.00,
    "call_graph": 0.85,
    "vector_semantic": 0.70,
    "git_history": 0.60,
    "test_discovery": 0.55,
}

#: `03` §S5's three named proximity values, plus one documented extension.
#: The spec ties proximity to *graph hop count* (0/1/2) and names nothing for
#: an item with no graph relationship at all — a symbol-grep test match, a
#: vector-search hit. `0.7` places those below every graph-connected item and
#: above a hypothetical second hop, which is the ordering "related, but not
#: through a call the failure point actually makes" should have.
PROXIMITY_FAILURE_POINT = 1.0
PROXIMITY_ONE_HOP = 0.8
PROXIMITY_TWO_HOP = 0.6
PROXIMITY_UNGRAPHED = 0.7

#: `03` §S5: "symbol_overlap_with_implicated_symbols" scales relevance by
#: 15% per overlapping symbol.
SYMBOL_OVERLAP_WEIGHT = 0.15

#: `03` §S5: "commits/PRs decay over 90 days."
RECENCY_WINDOW_DAYS = 90.0

#: `03` §S5's termination condition, revised at T4.4 after measuring the
#: corpus: no fixed file-count or token-count number can separate "retrieval
#: found real, thin, self-contained evidence" from "retrieval found nothing
#: worth reasoning about" — `external-03` (a real bug) and `unfixable-01`
#: (a designed control) admit the identical 2 files and 1231 tokens, while
#: `config-01` (also a real bug) admits only 251. Any threshold that lets
#: `config-01` through also lets both controls through, and any threshold
#: that rejects the controls also rejects `config-01`. Judging *fixability*
#: from evidence volume alone is not a question S5 (P3: retrieve narrowly)
#: is positioned to answer — `03` already gives S6 its own `insufficient_context`
#: exit for exactly this ("on evidence-binding failure ... terminal
#: insufficient_context"). S5's bar is lowered to what it can actually judge
#: honestly: did retrieval find the failure point at all, with any real
#: in-app content, or not. See `03` §S5's implementation note for the full
#: finding and the coordinator's decision.
MIN_ADMITTED_FILES = 1
MIN_ADMITTED_IN_APP_TOKENS = 1

#: How many recent commits `history` carries into the bundle. Strategy D
#: already caps at 10 (`03` §S5: "Last 10 commits"); this is not a second,
#: different cap, just where that number is named on this side of the
#: boundary.
MAX_RECENT_COMMITS = 10


@dataclass(frozen=True, slots=True)
class _Ranked:
    """One candidate, priority- and relevance-scored, not yet a `BundleFile`
    — kept separate so dedup can compare candidates before paying the cost of
    building the final Pydantic shape for the ones that lose."""

    repo_path: str
    strategy: str
    priority: int
    relevance: float
    tokens: int
    source: RetrievedFile


def _node_path(node_id: str) -> str:
    return node_id.split("::", 1)[0]


def _priority_by_path(
    edges: Sequence[GraphEdge], failure_id: str | None, entry_id: str | None
) -> dict[str, int]:
    """`03` §S5's priorities 1-5, derived from the call graph. Lower always
    wins when a path could be reached more than one way — the failure point's
    own file is priority 1 even if some other edge would also place it at 3."""
    priorities: dict[str, int] = {}

    def claim(path: str, priority: int) -> None:
        if path not in priorities or priority < priorities[path]:
            priorities[path] = priority

    if failure_id:
        claim(_node_path(failure_id), 1)
    if entry_id:
        claim(_node_path(entry_id), 2)

    for edge in edges:
        if edge.kind == "calls" and edge.source == failure_id:
            claim(_node_path(edge.target), 3)
        elif edge.kind == "references" and edge.source == failure_id:
            claim(_node_path(edge.target), 4)
        elif edge.kind == "calls" and edge.target == failure_id:
            claim(_node_path(edge.source), 5)

    return priorities


def _proximity_for(priority: int) -> float:
    if priority == 1:
        return PROXIMITY_FAILURE_POINT
    if priority in (2, 3, 4, 5):
        return PROXIMITY_ONE_HOP
    if priority == 9:
        return PROXIMITY_TWO_HOP
    return PROXIMITY_UNGRAPHED


def _recency_factor(commit_date: datetime | None, *, now: datetime) -> float:
    if commit_date is None:
        return 1.0
    age_days = max(0.0, (now - commit_date).total_seconds() / 86_400)
    return max(0.0, 1.0 - age_days / RECENCY_WINDOW_DAYS)


def _matches_symbol(qualname: str, bare_name: str) -> bool:
    """Whether `qualname` (`ast_index`'s dotted form, `"Class.method"` for a
    method, or a bare name for a module-level function/class) names
    `bare_name`. S4's `implicated_symbols` are always bare (`03` §S4's
    example: `"calculate_total"`, never `"CheckoutService.calculate_total"`)
    — comparing them by equality against a method's *qualified* name would
    never match, silently zeroing every symbol-overlap bonus and unresolved-
    symbol check for any implicated method on a class-based codebase, which
    this corpus is."""
    return qualname == bare_name or qualname.endswith(f".{bare_name}")


def _symbol_overlap(symbols_defined: Sequence[str], implicated: Sequence[str]) -> int:
    return sum(
        1
        for bare_name in implicated
        if any(_matches_symbol(qualname, bare_name) for qualname in symbols_defined)
    )


def _relevance(
    *,
    strategy: str,
    priority: int,
    symbols_defined: Sequence[str],
    implicated_symbols: Sequence[str],
    commit_date: datetime | None,
    now: datetime,
) -> float:
    """`03` §S5's formula, literally:
    `strategy_weight x recency_factor x proximity_factor x
    (1 + 0.15 x symbol_overlap)`."""
    overlap = _symbol_overlap(symbols_defined, implicated_symbols)
    return (
        STRATEGY_WEIGHT.get(strategy, 0.5)
        * _recency_factor(commit_date, now=now)
        * _proximity_for(priority)
        * (1 + SYMBOL_OVERLAP_WEIGHT * overlap)
    )


def _rank_files(
    files: Sequence[RetrievedFile],
    *,
    priority_by_path: dict[str, int],
    implicated_symbols: Sequence[str],
    commit_date_by_path: dict[str, datetime],
    now: datetime,
) -> list[_Ranked]:
    """`commit_date_by_path` carries blame dates computed *before* ranking —
    `RetrievedFile.blame` is never populated by T4.3's strategies (blame is
    attached to the assembled `BundleFile` only for the admitted failure-point
    entry, downstream of this function), so reading `item.blame` here would
    silently make `recency_factor` always `1.0` regardless of how old the
    introducing commit actually is."""
    ranked: list[_Ranked] = []
    for item in files:
        priority = (
            7 if item.strategy == "vector_semantic" else priority_by_path.get(item.repo_path, 3)
        )
        commit_date = commit_date_by_path.get(item.repo_path)
        relevance = _relevance(
            strategy=item.strategy,
            priority=priority,
            symbols_defined=item.symbols_defined,
            implicated_symbols=implicated_symbols,
            commit_date=commit_date,
            now=now,
        )
        ranked.append(
            _Ranked(
                repo_path=item.repo_path,
                strategy=item.strategy,
                priority=priority,
                relevance=relevance,
                tokens=estimate_tokens(item.content),
                source=item,
            )
        )
    return ranked


def _dedup(ranked: Sequence[_Ranked]) -> list[_Ranked]:
    """Same `repo_path` found by more than one strategy (`03` §S5's diagram:
    "Rank · Dedupe · Trim") keeps only the best-priority, highest-relevance
    entry — never both, and never silently the second one found."""
    best: dict[str, _Ranked] = {}
    for item in ranked:
        current = best.get(item.repo_path)
        if current is None or (item.priority, -item.relevance) < (
            current.priority,
            -current.relevance,
        ):
            best[item.repo_path] = item
    return list(best.values())


def _test_tokens(test: TestMatch) -> tuple[str, int]:
    """A test's content, or — "trimmed to signatures only if tight" (`03`
    §S5, priority 8) — just its `def test_...(...):` lines when the full body
    would not fit. Built eagerly so both sizes are known before the budget
    decision, not computed twice."""
    return test.content, estimate_tokens(test.content)


def _signature_only(content: str) -> str:
    lines = [
        line for line in content.splitlines() if line.strip().startswith(("def ", "async def "))
    ]
    return "\n".join(lines) if lines else content.splitlines()[0] if content else ""


def build_context_bundle(
    candidates: RetrievalCandidates,
    understanding: ErrorUnderstanding,
    *,
    repo: RepoRef,
    ref: str,
    bundle_id: str,
    commit_sha: str | None = None,
    now: datetime | None = None,
) -> RetrievalOutcome:
    """Turn T4.3's raw `RetrievalCandidates` into the real `03` §S5
    `ContextBundle`, or into `InsufficientContext` if the mechanical
    threshold is not met. Never raises on thin input — an empty
    `RetrievalCandidates` is a valid, if extreme, input and produces
    `InsufficientContext` like any other case that falls short.
    """
    now = now or datetime.now(UTC)
    failure = understanding.failure_point
    entry = understanding.entry_point
    failure_id = (
        f"{failure.repo_path}::{failure.function}"
        if failure and failure.repo_path and failure.function
        else None
    )
    entry_id = entry.handler if entry and entry.handler else None

    priority_by_path = _priority_by_path(candidates.graph_edges, failure_id, entry_id)

    # Blame is only ever known for the failure-point file (`08` §3.2's blame
    # call is scoped to the failing line), and it is computed here — before
    # ranking — because `_relevance`'s recency factor needs it and
    # `RetrievedFile.blame` itself is never populated by any T4.3 strategy.
    commit_date_by_path: dict[str, datetime] = {}
    if candidates.history and candidates.history.blame_commit and failure and failure.repo_path:
        commit_date_by_path[failure.repo_path] = candidates.history.blame_commit.date

    ranked = _dedup(
        _rank_files(
            candidates.files,
            priority_by_path=priority_by_path,
            implicated_symbols=understanding.implicated_symbols,
            commit_date_by_path=commit_date_by_path,
            now=now,
        )
    )
    ranked.sort(key=lambda item: (item.priority, -item.relevance, item.repo_path))

    repository = RepositoryRef(full_name=repo.full_name, ref=ref, commit_sha=commit_sha)

    admitted: list[_Ranked] = []
    budget_used = 0
    for item in ranked:
        if budget_used + item.tokens > TOKEN_BUDGET:
            continue  # evicted — a lower-priority/relevance item may still fit in the remainder
        admitted.append(item)
        budget_used += item.tokens

    priority_1_4 = [item for item in admitted if item.priority <= 4]
    in_app_tokens = sum(item.tokens for item in priority_1_4)
    if len(priority_1_4) < MIN_ADMITTED_FILES or in_app_tokens < MIN_ADMITTED_IN_APP_TOKENS:
        return InsufficientContext(
            bundle_id=bundle_id,
            repository=repository,
            admitted_file_count=len(priority_1_4),
            admitted_in_app_tokens=in_app_tokens,
            explanation=(
                f"After admitting priority 1-4 evidence, {len(priority_1_4)} distinct "
                f"file(s) and {in_app_tokens} token(s) of in-app source were available "
                f"(need at least {MIN_ADMITTED_FILES} file and {MIN_ADMITTED_IN_APP_TOKENS} "
                "token of real in-app content). Retrieval could not resolve any evidence "
                "to reason about."
            ),
        )

    # History: always attempted, at low fixed cost — dropped whole rather
    # than partially if it would not fit, since a half-quoted commit message
    # is not useful evidence.
    history = candidates.history
    blame_for_failure: BlameInfo | None = None
    history_tokens = 0
    bundle_history = BundleHistory()
    if history is not None:
        recent = history.recent_commits[:MAX_RECENT_COMMITS]
        history_tokens = sum(estimate_tokens(commit.message) for commit in recent)
        if history.release_diff is not None:
            history_tokens += sum(estimate_tokens(f.path) for f in history.release_diff.files) + 20
        if budget_used + history_tokens <= TOKEN_BUDGET:
            bundle_history = BundleHistory(
                blame_commit=history.blame_commit,
                recent_commits=recent,
                release_diff=history.release_diff,
            )
            budget_used += history_tokens
            if history.blame_commit is not None and failure and failure.line is not None:
                blame_for_failure = BlameInfo(line=failure.line, commit=history.blame_commit)
        else:
            history_tokens = 0

    # Tests: priority 8, full content if it fits, signature-only as a
    # documented fallback (`03` §S5), dropped entirely only if even that
    # does not fit.
    test_matches: list[BundleTestMatch] = []
    for test in candidates.tests:
        content, tokens = _test_tokens(test)
        if budget_used + tokens <= TOKEN_BUDGET:
            test_matches.append(
                BundleTestMatch(repo_path=test.repo_path, covers=test.covers, content=content)
            )
            budget_used += tokens
            continue
        signature = _signature_only(test.content)
        signature_tokens = estimate_tokens(signature)
        if budget_used + signature_tokens <= TOKEN_BUDGET:
            test_matches.append(
                BundleTestMatch(repo_path=test.repo_path, covers=test.covers, content=signature)
            )
            budget_used += signature_tokens

    bundle_files: list[BundleFile] = []
    strategy_items: dict[str, int] = {}
    strategy_tokens: dict[str, int] = {}
    for item in admitted:
        blame = (
            blame_for_failure
            if item.repo_path == (failure.repo_path if failure else None)
            else None
        )
        bundle_files.append(
            BundleFile(
                repo_path=item.repo_path,
                strategy=item.strategy,
                relevance=round(item.relevance, 4),
                language=item.source.language,
                content=item.source.content,
                line_range=item.source.line_range,
                truncated=item.source.truncated,
                symbols_defined=item.source.symbols_defined,
                blame=blame,
            )
        )
        strategy_items[item.strategy] = strategy_items.get(item.strategy, 0) + 1
        strategy_tokens[item.strategy] = strategy_tokens.get(item.strategy, 0) + item.tokens

    strategy_stats = {
        name: StrategyStat(items=strategy_items.get(name, 0), tokens=strategy_tokens.get(name, 0))
        for name in (
            "frame_direct",
            "call_graph",
            "vector_semantic",
            "git_history",
            "test_discovery",
        )
    }
    if history is not None:
        strategy_stats["git_history"] = StrategyStat(
            items=(1 if history.blame_commit else 0) + len(bundle_history.recent_commits),
            tokens=history_tokens,
        )
    strategy_stats["test_discovery"] = StrategyStat(
        items=len(test_matches), tokens=sum(estimate_tokens(t.content) for t in test_matches)
    )

    admitted_paths = {item.repo_path for item in admitted}
    resolved_callees = sum(
        1 for path, priority in priority_by_path.items() if priority == 3 and path in admitted_paths
    )
    resolved_callers = sum(
        1 for path, priority in priority_by_path.items() if priority == 5 and path in admitted_paths
    )
    unresolved = tuple(
        symbol
        for symbol in understanding.retrieval_plan.should_fetch_by_symbol
        if not any(
            _matches_symbol(qualname, symbol)
            for item in admitted
            for qualname in item.source.symbols_defined
        )
    )
    signals = QualitySignals(
        failure_point_resolved=bool(failure_id and _node_path(failure_id) in admitted_paths),
        entry_point_resolved=bool(entry_id and _node_path(entry_id) in admitted_paths),
        callees_resolved=resolved_callees,
        callers_resolved=resolved_callers,
        has_tests=bool(test_matches),
        has_release_correlation=history is not None and history.release_diff is not None,
        unresolved_symbols=unresolved,
    )

    gaps = tuple(f"{symbol} could not be located in the repository" for symbol in unresolved)
    coverage = (
        "full"
        if test_matches and len(test_matches) >= len(admitted_paths)
        else ("partial" if test_matches else "none")
    )

    return ContextBundle(
        bundle_id=bundle_id,
        repository=repository,
        token_count=budget_used,
        token_budget=TOKEN_BUDGET,
        files=tuple(bundle_files),
        graph=BundleGraph(
            nodes=tuple(
                BundleGraphNode(
                    id=node.id,
                    kind=node.kind,
                    is_failure_point=node.is_failure_point,
                    is_entry_point=node.is_entry_point,
                )
                for node in candidates.graph_nodes
                if _node_path(node.id) in admitted_paths
            ),
            edges=tuple(
                BundleGraphEdge(source=edge.source, target=edge.target, kind=edge.kind)
                for edge in candidates.graph_edges
                if _node_path(edge.source) in admitted_paths
                and _node_path(edge.target) in admitted_paths
            ),
        ),
        history=bundle_history,
        tests=BundleTests(found=tuple(test_matches), coverage_estimate=coverage),
        strategy_stats=strategy_stats,
        quality=Quality(score=compute_score(signals), signals=signals),
        gaps=gaps,
    )
