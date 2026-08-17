"""T4.3's intermediate contracts (`03` §S5's five strategies, before ranking).

Plain frozen dataclasses, not Pydantic — consistent with `github/types.py` and
`understand/frames.py`'s internal types. `03` §1 R4 requires Pydantic at a
*stage boundary*, because that is where an LLM's output must be validated at
the wire; nothing here crosses one yet. `RetrievalCandidates` is raw strategy
output, deliberately not the `ContextBundle` of `03` §S5's output contract —
ranking, deduplication, the 24,000-token budget, and quality scoring are
T4.4's, and building the real (Pydantic) `ContextBundle` belongs there, once
there is a relevance formula to populate it with.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from roottrace_worker.github.types import Commit, CompareResult


@dataclass(frozen=True, slots=True)
class BlameInfo:
    line: int
    commit: Commit


@dataclass(frozen=True, slots=True)
class RetrievedFile:
    """One file entry, matching `03` §S5's `files[]` shape minus `relevance`
    (T4.4 computes that from strategy weight, recency, proximity, and symbol
    overlap — none of which a single strategy can determine alone)."""

    repo_path: str
    strategy: (
        str  # "frame_direct" | "call_graph" | "vector_semantic" | "git_history" | "test_discovery"
    )
    language: str
    content: str
    line_range: tuple[int, int]
    truncated: bool
    symbols_defined: tuple[str, ...] = ()
    blame: BlameInfo | None = None


@dataclass(frozen=True, slots=True)
class GraphNode:
    id: str  # "repo_path::qualname"
    kind: str  # "function" | "class"
    is_failure_point: bool = False
    is_entry_point: bool = False


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: str
    target: str
    kind: str  # "calls" | "references"


@dataclass(frozen=True, slots=True)
class CallGraphResult:
    files: tuple[RetrievedFile, ...]
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


@dataclass(frozen=True, slots=True)
class HistoryCandidates:
    """Strategy D's output. `release_diff` is `None` whenever the caller does
    not supply `previous_ref` — determining "the previous release"
    automatically needs a releases table this ticket has no access to (`15`
    T4.3); the mechanism is built and tested, not the auto-lookup."""

    blame_commit: Commit | None
    recent_commits: tuple[Commit, ...]
    release_diff: CompareResult | None = None


@dataclass(frozen=True, slots=True)
class TestMatch:
    repo_path: str
    content: str
    covers: tuple[str, ...]  # implicated symbols this test file references


@dataclass(frozen=True, slots=True)
class RetrievalCandidates:
    """Everything the four (of five) implemented strategies found, unranked,
    undeduplicated, unbudgeted. T4.4 turns this into the real `ContextBundle`."""

    files: tuple[RetrievedFile, ...] = ()
    graph_nodes: tuple[GraphNode, ...] = ()
    graph_edges: tuple[GraphEdge, ...] = ()
    history: HistoryCandidates | None = None
    tests: tuple[TestMatch, ...] = field(default_factory=tuple)
