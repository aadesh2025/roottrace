"""The `ContextBundle` (`03` §S5's output contract) — T4.4.

**Pydantic, unlike the rest of `pipeline/retrieve`.** `03` §1 R4 requires a
validated model at a *stage boundary*; `contracts.py`'s dataclasses are all
intermediate, pre-ranking working data that never crosses one. This is the
first thing in the package that does — `ContextBundle` is what S6 receives,
`03` §S5's literal output contract, and the object `quality.score` rides into
S11's confidence calculation on. Consistent with `ErrorUnderstanding` at T4.1
being the one Pydantic model in an otherwise-dataclass `understand` package,
for the same reason.

`InsufficientContext` is the other terminal shape this stage can produce
(`03` §S5: *"the stage terminates the investigation as `insufficient_context`
with an explanation. We do not guess."*). `RetrievalOutcome` is the union of
the two — the caller (eventually the orchestrator, T8.2) is meant to branch on
which one came back, not to treat a thin bundle as though it were a normal one.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from roottrace_worker.github.types import Commit, CompareResult


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)


class RepositoryRef(_Contract):
    full_name: str
    ref: str
    id: str | None = None
    commit_sha: str | None = None


class BlameInfo(_Contract):
    line: int
    commit: Commit


class BundleFile(_Contract):
    """One `03` §S5 `files[]` entry — `contracts.RetrievedFile` plus the
    `relevance` T4.4's ranking formula computed for it."""

    repo_path: str
    strategy: str
    relevance: float
    language: str
    content: str
    line_range: tuple[int, int]
    truncated: bool
    symbols_defined: tuple[str, ...] = ()
    blame: BlameInfo | None = None


class BundleGraphNode(_Contract):
    id: str
    kind: str
    is_failure_point: bool = False
    is_entry_point: bool = False


class BundleGraphEdge(_Contract):
    source: str
    target: str
    kind: str


class BundleGraph(_Contract):
    nodes: tuple[BundleGraphNode, ...] = ()
    edges: tuple[BundleGraphEdge, ...] = ()


class BundleHistory(_Contract):
    blame_commit: Commit | None = None
    recent_commits: tuple[Commit, ...] = ()
    release_diff: CompareResult | None = None
    #: Always empty in V1 — no gateway method returns open PRs (`04` §7's
    #: worker access; `08` §7.2's interface has none either). Carried rather
    #: than omitted, same reasoning as `03` §S5's own `open_prs: []`.
    open_prs: tuple[str, ...] = ()


class BundleTestMatch(_Contract):
    repo_path: str
    covers: tuple[str, ...]
    content: str


class BundleTests(_Contract):
    found: tuple[BundleTestMatch, ...] = ()
    #: "full" | "partial" | "none" — see `ranking.py`'s `coverage_estimate`.
    coverage_estimate: str = "none"


class StrategyStat(_Contract):
    items: int
    tokens: int


class QualitySignals(_Contract):
    failure_point_resolved: bool
    entry_point_resolved: bool
    callees_resolved: int
    callers_resolved: int
    has_tests: bool
    has_release_correlation: bool
    unresolved_symbols: tuple[str, ...] = ()


class Quality(_Contract):
    score: float
    signals: QualitySignals


class ContextBundle(_Contract):
    bundle_id: str
    repository: RepositoryRef
    token_count: int
    token_budget: int
    files: tuple[BundleFile, ...]
    graph: BundleGraph
    history: BundleHistory
    tests: BundleTests
    strategy_stats: dict[str, StrategyStat]
    quality: Quality
    gaps: tuple[str, ...] = ()


class InsufficientContext(_Contract):
    """`03` §S5's other terminal shape. `reason` is one of the two mechanical
    triggers stated in the spec — never a model's opinion, since S5 has no
    model call in it at all: retrieval either mechanically found enough or it
    did not."""

    bundle_id: str
    repository: RepositoryRef
    admitted_file_count: int
    admitted_in_app_tokens: int
    explanation: str


RetrievalOutcome = ContextBundle | InsufficientContext
