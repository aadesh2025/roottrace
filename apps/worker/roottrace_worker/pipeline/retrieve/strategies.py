"""S5's five retrieval strategies (`03` §S5), run over one `ErrorUnderstanding`.

Four of five are implemented — A, B, D, E. **Strategy C (vector semantic
search) is deferred, per `03` §S5 itself**: the code path exists and returns
empty, because `code_nodes.embedding` is never populated in V1 (CLAUDE.md's
V1 boundaries: "repo indexing/embeddings ... stays empty"). Calling it is
therefore harmless and forward-compatible — nothing downstream needs to know
strategy C did nothing.

**`gather` is the entry point.** It runs strategy A first (every other
strategy either reads its output or reads independently-fetched content, and
nothing here needs A's specific *windowed* content — B, D and E always fetch
their own full files), then B, D and E concurrently. `03` §S5's diagram shows
all five running in parallel; V1 runs A ahead of the rest because nothing
downstream needs it to finish sooner, and doing so costs nothing measurable
against a local fixture transport. True wall-clock parallelism across all
five is a latency optimisation for a live transport with real HTTP round
trips, which V1 does not have.

**What this module deliberately does not do: rank, deduplicate, or budget.**
`03` §S5's diagram draws "Rank · Dedupe · Trim to token budget" as a distinct
box after the five strategies, and T4.4 owns it — the relevance formula needs
recency and proximity factors this module has no reason to compute, and the
24,000-token cap needs a real tokenizer neither T4.1 nor T4.2 introduced.
Strategy A and strategy B routinely return the same file (a frame that is
also a caller, say); that overlap is expected here and is exactly what T4.4's
dedup step exists to resolve.
"""

from __future__ import annotations

import asyncio
import posixpath
from collections.abc import Sequence

from roottrace_worker.github.errors import FileNotFound, RefNotFound
from roottrace_worker.github.gateway import GitHubGateway
from roottrace_worker.github.types import RepoRef, RepoTree
from roottrace_worker.pipeline.retrieve.ast_index import (
    FunctionInfo,
    ModuleIndex,
    analyze_calls,
    build_index,
    enclosing_function,
    find_class,
    find_function,
)
from roottrace_worker.pipeline.retrieve.contracts import (
    CallGraphResult,
    GraphEdge,
    GraphNode,
    HistoryCandidates,
    RetrievalCandidates,
    RetrievedFile,
    TestMatch,
)
from roottrace_worker.pipeline.retrieve.import_resolution import resolve_import
from roottrace_worker.pipeline.retrieve.path_resolution import resolve_against_tree
from roottrace_worker.pipeline.retrieve.windowing import extract_window
from roottrace_worker.pipeline.understand.contracts import ErrorUnderstanding, FailurePoint, Frame
from roottrace_worker.pipeline.understand.frames import ResolvedPath

#: `03` §S5: 1 hop for V1 ("2 hops only if budget remains" — budget is T4.4's
#: to know about, so V1 never takes the second hop).
MAX_HOPS = 1

#: How many candidate caller *files* strategy B will fetch and Tree-sitter
#: [`ast`]-confirm per failure-point function. `search_symbol`'s hits include
#: every textual mention — comments, docstrings — of the function's name;
#: this bounds how much of that noise gets turned into a fetch, independent
#: of how much of it turns out to be a real call once confirmed.
MAX_CALLER_CANDIDATES = 8


# ── Strategy A — frame-direct fetch (weight 1.00) ───────────────────────────


async def strategy_a_frame_direct(
    gateway: GitHubGateway,
    repo: RepoRef,
    ref: str,
    tree: RepoTree,
    frames: Sequence[Frame],
    *,
    scope_root: str = "",
) -> tuple[RetrievedFile, ...]:
    """`03` §S5 strategy A. One entry per distinct `in_app` frame file — when
    more than one frame lands in the same file (`null-prop-04`), their
    windows are unioned into a single enclosing range rather than producing
    two overlapping entries for one path.

    **Every frame's path is re-verified against `tree` here, not trusted from
    S4.** `understanding.frames[].repo_path` is only cascade steps 1-2 (`03`
    §S4 — S4 has no repo access to check it against); `config-02` is a
    well-formed path from those steps that is not a real file, and fetching
    it blindly would silently drop the frame instead of finding the real one.
    `resolve_against_tree` (T4.2) is what completes the cascade — this is the
    first place in the real pipeline that calls it, per `15` T4.2's own note
    that S5's fetch loop is where that re-verification happens.
    """
    by_path: dict[str, list[Frame]] = {}
    for frame in frames:
        if not frame.in_app:
            continue
        candidate = ResolvedPath(repo_path=frame.repo_path, confidence=frame.confidence)
        resolution = resolve_against_tree(frame.raw_path, candidate, tree, scope_root=scope_root)
        if resolution.repo_path is None:
            continue
        by_path.setdefault(resolution.repo_path, []).append(frame)

    items: list[RetrievedFile] = []
    for repo_path, path_frames in by_path.items():
        try:
            fetched = await gateway.fetch_file(repo, repo_path, ref)
        except FileNotFound:
            continue

        index = build_index(fetched.content)
        ranges: list[tuple[int, int]] = []
        for frame in path_frames:
            if frame.line is None:
                continue
            enclosing = enclosing_function(index, frame.line) if index else None
            ranges.append(
                (enclosing.start_line, enclosing.end_line)
                if enclosing
                else (frame.line, frame.line)
            )
        center = (min(r[0] for r in ranges), max(r[1] for r in ranges)) if ranges else None

        window, line_range, truncated = extract_window(fetched.content, center_range=center)
        symbols = _defined_within(index, line_range)
        items.append(
            RetrievedFile(
                repo_path=repo_path,
                strategy="frame_direct",
                language="python",
                content=window,
                line_range=line_range,
                truncated=truncated,
                symbols_defined=symbols,
            )
        )
    return tuple(items)


def _defined_within(index: ModuleIndex | None, line_range: tuple[int, int]) -> tuple[str, ...]:
    """Every function, method, and class whose definition starts inside the
    window — `03` §S5's `symbols_defined` example lists a class (`TaxClient`)
    alongside a function (`get_rate`) in the same list, so a class with no
    methods (a bare `@dataclass`) must not be silently absent."""
    if index is None:
        return ()
    low, high = line_range
    functions = (f.qualname for f in index.functions if low <= f.start_line <= high)
    classes = (c.name for c in index.classes if low <= c.start_line <= high)
    return (*functions, *classes)


# ── Strategy B — call-graph expansion (weight 0.85) ─────────────────────────


def _resolve_failure_path(
    understanding: ErrorUnderstanding, tree: RepoTree, failure: FailurePoint
) -> str | None:
    """`failure.repo_path`, re-verified against `tree` — it is S4's cascade
    steps 1-2 alone (`03` §S4 has no repo access), the same gap strategy A
    closes for every frame. `understand.plan.failure_point` builds
    `FailurePoint` from the innermost `in_app` frame, so that frame's
    `raw_path`/`confidence` is the candidate to re-verify; if no such frame
    can be found (should not happen, since `failure` came from one), the
    already-unverified path is used as a last resort rather than discarding
    a failure point entirely."""
    source_frame = next(
        (frame for frame in understanding.frames if frame.in_app),
        None,
    )
    if source_frame is None:
        return failure.repo_path

    candidate = ResolvedPath(repo_path=failure.repo_path, confidence=source_frame.confidence)
    resolution = resolve_against_tree(source_frame.raw_path, candidate, tree)
    return resolution.repo_path


async def strategy_b_call_graph(
    gateway: GitHubGateway,
    repo: RepoRef,
    ref: str,
    tree: RepoTree,
    understanding: ErrorUnderstanding,
) -> CallGraphResult:
    """`03` §S5 strategy B: callees (1 hop), callers, and type definitions
    around the failure-point function. Imports resolution
    (`import_resolution.py`) backs callee/type resolution rather than being a
    separate output — `03` §S5 lists it as its own bullet, but what it
    describes ("resolve local imports to repo paths and fetch those that
    define implicated symbols") is exactly how callee/type resolution already
    works here, and a fourth pass repeating that work would only refetch the
    same files under a different label.

    The failure point's own path is re-verified against `tree` before use,
    the same reason and the same mechanism as strategy A — `failure_point` is
    built from S4's cascade steps 1-2 alone (`03` §S4, no repo access), and a
    well-formed-but-wrong path here would make the whole strategy return
    nothing rather than expand from the corrected location.
    """
    failure = understanding.failure_point
    if failure is None or not failure.repo_path or not failure.function:
        return CallGraphResult(files=(), nodes=(), edges=())

    tree_paths = frozenset(tree.paths())
    resolved_failure_path = _resolve_failure_path(understanding, tree, failure)
    if resolved_failure_path is None:
        return CallGraphResult(files=(), nodes=(), edges=())
    failure_repo_path = resolved_failure_path

    failure_id = f"{failure_repo_path}::{failure.function}"
    nodes: list[GraphNode] = [GraphNode(id=failure_id, kind="function", is_failure_point=True)]
    edges: list[GraphEdge] = []
    files: list[RetrievedFile] = []
    resolved_files: set[str] = set()

    try:
        source = await gateway.fetch_file(repo, failure_repo_path, ref)
    except FileNotFound:
        return CallGraphResult(files=(), nodes=tuple(nodes), edges=())

    index = build_index(source.content)
    if index is None:
        return CallGraphResult(files=(), nodes=tuple(nodes), edges=())

    func = find_function(index, failure.function, line=failure.line)
    if func is None:
        return CallGraphResult(files=(), nodes=tuple(nodes), edges=())

    callees, type_names = analyze_calls(func.node)

    # `03` §S5: "1 hop; 2 hops only if budget remains." `MAX_HOPS` is fixed at
    # 1 for V1 — a second hop needs T4.4's budget to know whether there is
    # room for it, which does not exist yet.
    for name in callees:
        await _expand_reference(
            gateway,
            repo,
            ref,
            tree_paths,
            index,
            source_path=failure_repo_path,
            name=name,
            kinds=("function",),
            edge_kind="calls",
            origin_id=failure_id,
            files=files,
            nodes=nodes,
            edges=edges,
            resolved_files=resolved_files,
            reverse_edge=False,
        )

    for name in type_names:
        await _expand_reference(
            gateway,
            repo,
            ref,
            tree_paths,
            index,
            source_path=failure_repo_path,
            name=name,
            kinds=("class",),
            edge_kind="references",
            origin_id=failure_id,
            files=files,
            nodes=nodes,
            edges=edges,
            resolved_files=resolved_files,
            reverse_edge=False,
        )

    await _expand_callers(
        gateway,
        repo,
        ref,
        failure,
        failure_repo_path=failure_repo_path,
        files=files,
        nodes=nodes,
        edges=edges,
        failure_id=failure_id,
    )

    return CallGraphResult(files=tuple(files), nodes=tuple(nodes), edges=tuple(edges))


async def _expand_reference(
    gateway: GitHubGateway,
    repo: RepoRef,
    ref: str,
    tree_paths: frozenset[str],
    index: ModuleIndex,
    *,
    source_path: str,
    name: str,
    kinds: tuple[str, ...],
    edge_kind: str,
    origin_id: str,
    files: list[RetrievedFile],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    resolved_files: set[str],
    reverse_edge: bool,
) -> None:
    target_path, target_name = await _resolve_definition(
        gateway, repo, tree_paths, index, source_path=source_path, name=name, kinds=kinds
    )
    if target_path is None or target_name is None:
        return

    node_id = f"{target_path}::{target_name}"
    if target_path not in resolved_files:
        item = await _fetch_definition_window(gateway, repo, ref, target_path, target_name)
        if item is None:
            return
        resolved_files.add(target_path)
        files.append(item)
    kind = "class" if kinds == ("class",) else "function"
    if not any(existing.id == node_id for existing in nodes):
        nodes.append(GraphNode(id=node_id, kind=kind))
    source, target = (node_id, origin_id) if reverse_edge else (origin_id, node_id)
    edges.append(GraphEdge(source=source, target=target, kind=edge_kind))


async def _resolve_definition(
    gateway: GitHubGateway,
    repo: RepoRef,
    tree_paths: frozenset[str],
    index: ModuleIndex,
    *,
    source_path: str,
    name: str,
    kinds: tuple[str, ...],
) -> tuple[str | None, str | None]:
    """Where `name` is defined: same file, then a direct import, then a
    repository-wide search — `08` §3.2's cascade shape, reused for symbols
    instead of paths. Each step is tried only if the previous one misses."""
    if "function" in kinds and (local := find_function(index, name)) is not None:
        return source_path, local.name
    if "class" in kinds and (local_class := find_class(index, name)) is not None:
        return source_path, local_class.name

    for imp in index.imports:
        if imp.imported_name != name:
            continue
        resolved = resolve_import(
            tree_paths,
            importing_file=source_path,
            module=imp.module,
            level=imp.level,
            original_name=imp.original_name,
        )
        if resolved is not None:
            return resolved, imp.original_name or name

    hits = await gateway.search_symbol(repo, name)
    candidates = [hit for hit in hits if hit.kind in kinds]
    if not candidates:
        return None, None
    # Prefer production code over test stubs of the same name, then the
    # shallowest path — the same tiebreak T4.2 uses for an ambiguous suffix
    # match, reused because it answers the same question: which of several
    # equally-named candidates is most likely the real one.
    candidates.sort(key=lambda hit: (hit.path.startswith("tests/"), hit.path.count("/"), hit.path))
    chosen = candidates[0]
    return chosen.path, name


async def _fetch_definition_window(
    gateway: GitHubGateway, repo: RepoRef, ref: str, path: str, symbol_name: str
) -> RetrievedFile | None:
    try:
        fetched = await gateway.fetch_file(repo, path, ref)
    except FileNotFound:
        return None

    index = build_index(fetched.content)
    target: FunctionInfo | None = None
    center: tuple[int, int] | None = None
    if index is not None:
        target = find_function(index, symbol_name)
        if target is not None:
            center = (target.start_line, target.end_line)
        else:
            cls = find_class(index, symbol_name)
            if cls is not None:
                center = (cls.start_line, cls.end_line)

    window, line_range, truncated = extract_window(fetched.content, center_range=center)
    return RetrievedFile(
        repo_path=path,
        strategy="call_graph",
        language="python",
        content=window,
        line_range=line_range,
        truncated=truncated,
        symbols_defined=_defined_within(index, line_range),
    )


async def _expand_callers(
    gateway: GitHubGateway,
    repo: RepoRef,
    ref: str,
    failure: FailurePoint,
    *,
    failure_repo_path: str,
    files: list[RetrievedFile],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    failure_id: str,
) -> None:
    """`03` §S5: "Callers ... found via ... GitHub code search on the symbol
    name" — `code_edges` is never populated in V1, so this path always runs.
    `search_symbol` reports every textual occurrence (`08 §3.2`
    implementation note); a hit is trusted as a real caller only once this
    function's own `ast` parse of the candidate file confirms an actual call
    expression at that hit — a docstring or comment mentioning the function's
    name produces no such node and is silently dropped, not fetched twice.

    `failure_repo_path` is the tree-verified path (`_resolve_failure_path`),
    not `failure.repo_path` — excluding the failure point's own file from the
    caller search must exclude the file it actually lives in, or a corrected
    path would let the unverified one slip back in as a false "caller"."""
    if failure.function is None:
        return  # the caller already checked this; re-checked for mypy's narrowing, not defensively

    hits = await gateway.search_symbol(repo, failure.function)
    candidate_paths = sorted(
        {hit.path for hit in hits if hit.kind == "reference" and hit.path != failure_repo_path}
    )

    for candidate_path in candidate_paths[:MAX_CALLER_CANDIDATES]:
        try:
            candidate = await gateway.fetch_file(repo, candidate_path, ref)
        except FileNotFound:
            continue
        index = build_index(candidate.content)
        if index is None:
            continue

        caller = _confirmed_caller(index, failure.function)
        if caller is None:
            continue

        window, line_range, truncated = extract_window(
            candidate.content, center_range=(caller.start_line, caller.end_line)
        )
        files.append(
            RetrievedFile(
                repo_path=candidate_path,
                strategy="call_graph",
                language="python",
                content=window,
                line_range=line_range,
                truncated=truncated,
                symbols_defined=(caller.qualname,),
            )
        )
        node_id = f"{candidate_path}::{caller.qualname}"
        if not any(existing.id == node_id for existing in nodes):
            nodes.append(GraphNode(id=node_id, kind="function"))
        edges.append(GraphEdge(source=node_id, target=failure_id, kind="calls"))


def _confirmed_caller(index: ModuleIndex, symbol: str) -> FunctionInfo | None:
    """The first function in `index` whose body genuinely calls `symbol` —
    the `ast`-level confirmation that turns a `search_symbol` text hit into a
    trusted caller."""
    for candidate in index.functions:
        callees, _ = analyze_calls(candidate.node)
        if symbol in callees:
            return candidate
    return None


# ── Strategy C — vector semantic search (weight 0.70; deferred) ────────────


async def strategy_c_vector_semantic(
    semantic_queries: Sequence[str],
) -> tuple[RetrievedFile, ...]:
    """`03` §S5: the index is empty in V1; the code path exists and returns
    empty. `semantic_queries` is accepted and ignored rather than omitted, so
    a caller passing S4's plan through does not need a special case for this
    one strategy — the signature is what V2's `pgvector` implementation will
    fill in, not what it will add."""
    del semantic_queries
    return ()


# ── Strategy D — git history (weight 0.60) ──────────────────────────────────


async def strategy_d_git_history(
    gateway: GitHubGateway,
    repo: RepoRef,
    ref: str,
    *,
    must_fetch_paths: Sequence[str],
    failure_point: FailurePoint | None,
    blame_path: str | None = None,
    previous_ref: str | None = None,
) -> HistoryCandidates:
    """`03` §S5 strategy D. `release_diff` is `None` unless the caller
    supplies `previous_ref` — see `HistoryCandidates`'s docstring for why
    that lookup is not performed here.

    `blame_path` is the tree-verified failure-point path (`gather` resolves
    it once via `_resolve_failure_path` and shares it with strategy B, rather
    than each strategy re-deriving it); it falls back to
    `failure_point.repo_path` so a direct caller — a unit test, say — is not
    forced to pre-resolve a path that was already correct.
    """
    blame_commit = None
    path_for_blame = (
        blame_path
        if blame_path is not None
        else (failure_point.repo_path if failure_point else None)
    )
    if path_for_blame and failure_point and failure_point.line:
        ranges = await gateway.blame(
            repo, path_for_blame, ref, (failure_point.line, failure_point.line)
        )
        if ranges:
            blame_commit = ranges[0].commit

    seen_shas: set[str] = set()
    recent = []
    for path in must_fetch_paths:
        for commit in await gateway.recent_commits(repo, path, limit=10):
            if commit.sha not in seen_shas:
                seen_shas.add(commit.sha)
                recent.append(commit)
    recent.sort(key=lambda commit: commit.date, reverse=True)

    release_diff = None
    if previous_ref is not None:
        try:
            release_diff = await gateway.compare(repo, previous_ref, ref)
        except RefNotFound:
            release_diff = None

    return HistoryCandidates(
        blame_commit=blame_commit, recent_commits=tuple(recent[:10]), release_diff=release_diff
    )


# ── Strategy E — test discovery (weight 0.55) ───────────────────────────────


async def strategy_e_test_discovery(
    gateway: GitHubGateway,
    repo: RepoRef,
    ref: str,
    tree: RepoTree,
    *,
    must_fetch_paths: Sequence[str],
    implicated_symbols: Sequence[str],
) -> tuple[TestMatch, ...]:
    """`03` §S5 strategy E: convention (`services/checkout.py` ->
    `test_checkout.py`) and symbol grep, unioned."""
    tree_paths = tree.paths()
    covers: dict[str, set[str]] = {}

    for source_path in must_fetch_paths:
        stem = posixpath.basename(source_path)
        if stem.endswith(".py"):
            stem = stem[: -len(".py")]
        expected_names = {f"test_{stem}.py", f"{stem}_test.py"}
        for candidate in tree_paths:
            if posixpath.basename(candidate) in expected_names:
                covers.setdefault(candidate, set()).add(stem)

    for symbol in implicated_symbols:
        for hit in await gateway.search_symbol(repo, symbol):
            if _looks_like_a_test_path(hit.path):
                covers.setdefault(hit.path, set()).add(symbol)

    matches: list[TestMatch] = []
    for path in sorted(covers):
        try:
            fetched = await gateway.fetch_file(repo, path, ref)
        except FileNotFound:
            continue
        matches.append(
            TestMatch(repo_path=path, content=fetched.content, covers=tuple(sorted(covers[path])))
        )
    return tuple(matches)


def _looks_like_a_test_path(path: str) -> bool:
    segments = path.split("/")
    basename = segments[-1]
    return "tests" in segments[:-1] or basename.startswith("test_") or basename.endswith("_test.py")


# ── Orchestration ────────────────────────────────────────────────────────


async def gather(
    gateway: GitHubGateway,
    repo: RepoRef,
    ref: str,
    tree: RepoTree,
    understanding: ErrorUnderstanding,
    *,
    previous_ref: str | None = None,
) -> RetrievalCandidates:
    """Run strategies A, B, C (stub), D, E over one understanding and its
    plan. Returns raw, unranked, unbudgeted candidates — see this module's
    docstring for why ranking is deliberately not done here."""
    frame_files = await strategy_a_frame_direct(gateway, repo, ref, tree, understanding.frames)
    must_fetch = tuple(
        dict.fromkeys(
            (*understanding.retrieval_plan.must_fetch, *(f.repo_path for f in frame_files))
        )
    )
    blame_path = (
        _resolve_failure_path(understanding, tree, understanding.failure_point)
        if understanding.failure_point is not None
        else None
    )

    call_graph, _semantic, history, tests = await asyncio.gather(
        strategy_b_call_graph(gateway, repo, ref, tree, understanding),
        strategy_c_vector_semantic(understanding.retrieval_plan.semantic_queries),
        strategy_d_git_history(
            gateway,
            repo,
            ref,
            must_fetch_paths=must_fetch,
            failure_point=understanding.failure_point,
            blame_path=blame_path,
            previous_ref=previous_ref,
        ),
        strategy_e_test_discovery(
            gateway,
            repo,
            ref,
            tree,
            must_fetch_paths=must_fetch,
            implicated_symbols=understanding.implicated_symbols,
        ),
    )

    return RetrievalCandidates(
        files=(*frame_files, *call_graph.files),
        graph_nodes=call_graph.nodes,
        graph_edges=call_graph.edges,
        history=history,
        tests=tests,
    )
