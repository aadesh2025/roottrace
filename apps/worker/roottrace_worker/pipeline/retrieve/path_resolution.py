"""Frame path resolution, cascade steps 3-4 (`08` §3.2) — the S5 half.

Steps 1-2 (configured mappings, heuristic prefix stripping) live in S4
(`understand.frames.resolve_path`), because they need no repository access.
Steps 3-4 need the fetched tree, and `03` §8.1 draws the boundary explicitly:
*"S4 fetching a file to 'check' a path" is a boundary violation — S4 has no
repo access by design; it produces a plan.* S4's own failure-mode table sends
the unresolved case here: *"Path mapping produces no plausible repo path ...
S5 falls back to filename search across the tree."*

```
Step 3  Suffix matching against the cached repo tree
        find paths whose suffix matches the frame path's tail.
        Unique match -> 0.85.  Multiple -> prefer the longest common suffix,
        then the shallowest path                                  -> 0.60

Step 4  Filename-only search
        Last resort. Unique basename match -> 0.50. Ambiguous -> 0.30 and
        flag low_frame_confidence in the UI
```

**A step 1/2 result is trusted only once it is confirmed to exist in the
tree, never before.** `config-02` is why: heuristic prefix stripping produces
`services/services/export.py`, a well-formed path that is not a real file,
and nothing available to S4 could have known that. Here, the tree is
available, so the guess is checked — and corrected by suffix matching to
`services/export.py` when it fails.

**Ambiguity is reported, not guessed through.** When step 4 finds more than
one file with the same basename, `08` §3.2 says to flag it, not to pick one.
Silently choosing among candidates would look like a resolution and would be
a coin flip; `resolved=None` with `confidence=0.30` is the honest answer, and
matches the pattern the rest of this codebase uses for retrieval and eviction
("we do not guess").

**Monorepo scoping (`root_path` + `service_map`) is a hard filter, not a
preference.** `08` §3.2: *"scopes resolution to the right package before any
matching happens."* When the ingest event's `service` maps to a
subdirectory, steps 3-4 search only inside it — a match outside the scoped
package is not returned, even if it is the sole match in the whole tree.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from roottrace_worker.github.types import RepoTree, TreeEntry
from roottrace_worker.pipeline.understand.frames import (
    CONFIDENCE_CONFIGURED,
    CONFIDENCE_HEURISTIC,
    PathMapping,
    ResolvedPath,
)
from roottrace_worker.pipeline.understand.frames import (
    resolve_path as resolve_path_steps_1_2,
)

#: `08` §3.2's step 3/4 confidences. Steps 1-2's own values
#: (`CONFIDENCE_CONFIGURED`, `CONFIDENCE_HEURISTIC`) are imported above rather
#: than restated, so the two modules cannot drift on what "configured" means.
CONFIDENCE_SUFFIX_UNIQUE = 0.85
CONFIDENCE_SUFFIX_MULTIPLE = 0.60
CONFIDENCE_FILENAME_UNIQUE = 0.50
CONFIDENCE_FILENAME_AMBIGUOUS = 0.30
#: Not one of `08` §3.2's named steps — nothing matched anywhere, at any
#: step. Distinct from the ambiguous-match floor above: an ambiguous match at
#: least narrowed the search to a handful of real candidates; finding nothing
#: at all is a worse signal and is scored accordingly.
CONFIDENCE_NOT_FOUND = 0.0

_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:/")


@dataclass(frozen=True, slots=True)
class TreeResolution:
    repo_path: str | None
    confidence: float
    method: str


@dataclass(frozen=True, slots=True)
class PathMappingResult:
    """One row of `05` §6.6's `test_path_mapping` response."""

    input: str
    resolved: str | None
    confidence: float
    method: str
    exists_in_repo: bool


def _segments(path: str) -> tuple[str, ...]:
    """Path components, normalised. No drive letter, no empty leaders — a
    Windows absolute path and a POSIX one must produce the same tail."""
    normalised = path.replace("\\", "/")
    normalised = _DRIVE_PREFIX.sub("", normalised)
    return tuple(part for part in normalised.split("/") if part)


def resolve_scope(
    root_path: str | None, service_map: Mapping[str, str], service: str | None
) -> str:
    """The monorepo package a frame's resolution is confined to, if any.

    Empty means "search the whole tree" — a project with no `root_path`, or
    an event whose `service` has no entry in `service_map`, is not a monorepo
    scoping failure; it is simply not scoped.
    """
    root = (root_path or "").strip("/")
    sub = ((service_map.get(service, "") if service else "") or "").strip("/")
    return "/".join(part for part in (root, sub) if part)


def _pool(tree: RepoTree, scope_root: str) -> tuple[TreeEntry, ...]:
    blobs = tuple(entry for entry in tree.entries if entry.type == "blob")
    if not scope_root:
        return blobs
    prefix = scope_root + "/"
    return tuple(entry for entry in blobs if entry.path.startswith(prefix))


def _method_for_verified(confidence: float) -> str:
    if confidence >= CONFIDENCE_CONFIGURED:
        return "configured_mapping"
    if confidence >= CONFIDENCE_HEURISTIC:
        return "heuristic_prefix_strip"
    return "unresolved"  # pragma: no cover — steps 1-2 never emit a path here


def resolve_against_tree(
    raw_path: str,
    candidate: ResolvedPath,
    tree: RepoTree,
    *,
    scope_root: str = "",
) -> TreeResolution:
    """Cascade steps 3-4, given what steps 1-2 already produced.

    `candidate` is `understand.frames.resolve_path`'s output for this frame.
    Call `resolve_frame_path` instead of this directly unless steps 1-2 have
    already run and you are holding their result (T4.1's `preparse` is
    exactly that caller, once it gains repo access — see `15` T4.2).
    """
    pool = _pool(tree, scope_root)
    pool_paths = {entry.path for entry in pool}

    if candidate.repo_path is not None and candidate.repo_path in pool_paths:
        return TreeResolution(
            repo_path=candidate.repo_path,
            confidence=candidate.confidence,
            method=_method_for_verified(candidate.confidence),
        )

    basis = _segments(candidate.repo_path) if candidate.repo_path else _segments(raw_path)

    # Step 3 — suffix matching. Longest tail first (the full basis included,
    # since it may not have been the value steps 1-2 tried, or may not have
    # been tried at all when they produced no path); stop shortening the
    # moment something matches. Excludes length 1 — the bare basename is
    # step 4's, at a strictly lower confidence.
    for length in range(len(basis), 1, -1):
        suffix = "/".join(basis[-length:])
        matches = [
            entry for entry in pool if entry.path == suffix or entry.path.endswith("/" + suffix)
        ]
        if not matches:
            continue
        if len(matches) == 1:
            return TreeResolution(matches[0].path, CONFIDENCE_SUFFIX_UNIQUE, "suffix_match")
        shallowest = min(matches, key=lambda entry: entry.path.count("/"))
        return TreeResolution(shallowest.path, CONFIDENCE_SUFFIX_MULTIPLE, "suffix_match")

    # Step 4 — filename-only search. Last resort.
    if basis:
        basename = basis[-1]
        matches = [entry for entry in pool if posixpath.basename(entry.path) == basename]
        if len(matches) == 1:
            return TreeResolution(matches[0].path, CONFIDENCE_FILENAME_UNIQUE, "filename_search")
        if matches:
            return TreeResolution(None, CONFIDENCE_FILENAME_AMBIGUOUS, "filename_search")

    return TreeResolution(None, CONFIDENCE_NOT_FOUND, "unresolved")


def resolve_frame_path(
    raw_path: str,
    tree: RepoTree,
    *,
    mappings: Sequence[PathMapping] = (),
    scope_root: str = "",
) -> TreeResolution:
    """The full four-step cascade for one frame. What S5's fetch loop (T4.3)
    calls once it holds the repository tree."""
    candidate = resolve_path_steps_1_2(raw_path, mappings)
    return resolve_against_tree(raw_path, candidate, tree, scope_root=scope_root)


def dry_run_path_mapping(
    stack_paths: Sequence[str],
    tree: RepoTree,
    *,
    mappings: Sequence[PathMapping] = (),
    root_path: str | None = "",
    service_map: Mapping[str, str] | None = None,
    service: str | None = None,
) -> tuple[PathMappingResult, ...]:
    """`05` §6.6's `POST /v1/repositories/{id}/test_path_mapping`, as a pure
    function.

    This is the resolution logic the endpoint needs, not the endpoint. The
    route requires a `repositories` table row, an installation, and an
    authorization context that Phase 7 has no other reason to build yet —
    T4.2's acceptance criteria (`15` §6) are about the cascade and the
    monorepo scoping, not the HTTP surface, so the router is left for
    whichever ticket first needs `repositories` CRUD (`15` T4.2 records this
    explicitly). Wiring an endpoint to this function is a few lines once that
    exists.
    """
    scope_root = resolve_scope(root_path, service_map or {}, service)
    results: list[PathMappingResult] = []
    for raw in stack_paths:
        resolution = resolve_frame_path(raw, tree, mappings=mappings, scope_root=scope_root)
        results.append(
            PathMappingResult(
                input=raw,
                resolved=resolution.repo_path,
                confidence=resolution.confidence,
                method=resolution.method,
                exists_in_repo=resolution.repo_path is not None,
            )
        )
    return tuple(results)
