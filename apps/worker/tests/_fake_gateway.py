"""A minimal, in-memory `GitHubGateway` for strategy unit tests.

Not a second transport implementation — it satisfies the Protocol
structurally only for the read methods `strategies.py` actually calls, and
exists so strategy-level tests can control exactly what a symbol search or a
blame call returns without depending on `fixtures/synthetic-repo`'s real
content, which the corpus tests already exercise faithfully.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from roottrace_worker.github.errors import FileNotFound, RefNotFound
from roottrace_worker.github.types import (
    Actor,
    BlameRange,
    Commit,
    CompareResult,
    FileContent,
    PullRequestDraft,
    PullRequestRef,
    RepoRef,
    RepoTree,
    Sha,
    SymbolHit,
    TreeEntry,
)


@dataclass
class FakeGateway:
    """`files`: repo_path -> source. `blames`/`commits_by_path` are supplied
    directly rather than derived, since strategy tests care about what
    strategy D does with blame/commit *results*, not about re-deriving git
    history from scratch."""

    files: Mapping[str, str] = field(default_factory=dict)
    blames: Mapping[str, list[BlameRange]] = field(default_factory=dict)
    commits_by_path: Mapping[str, list[Commit]] = field(default_factory=dict)
    compares: Mapping[tuple[str, str], CompareResult] = field(default_factory=dict)
    #: Paths reported by `fetch_tree` but with no backing content — for
    #: testing the "resolved but then unfetchable" branches every strategy
    #: has to handle for a real, racy live transport.
    phantom_paths: tuple[str, ...] = ()
    #: Synthetic `search_symbol` hits for paths with no backing content —
    #: same purpose as `phantom_paths`, for the caller-search path, which
    #: finds candidates via search rather than via the tree.
    phantom_hits: tuple[SymbolHit, ...] = ()

    async def fetch_tree(self, repo: RepoRef, ref: str) -> RepoTree:
        entries = tuple(
            TreeEntry(path=path, sha="0" * 40)
            for path in sorted({*self.files, *self.phantom_paths})
        )
        return RepoTree(ref=ref, sha="1" * 40, entries=entries)

    async def fetch_file(self, repo: RepoRef, path: str, ref: str) -> FileContent:
        if path not in self.files:
            raise FileNotFound(path, ref)
        content = self.files[path]
        return FileContent(path=path, content=content, sha="0" * 40, ref=ref)

    async def fetch_files(self, repo: RepoRef, paths: Sequence[str], ref: str) -> list[FileContent]:
        return [await self.fetch_file(repo, path, ref) for path in paths]

    async def blame(
        self, repo: RepoRef, path: str, ref: str, line_range: tuple[int, int]
    ) -> list[BlameRange]:
        return list(self.blames.get(path, []))

    async def recent_commits(self, repo: RepoRef, path: str, limit: int = 10) -> list[Commit]:
        return list(self.commits_by_path.get(path, []))[:limit]

    async def compare(self, repo: RepoRef, base: str, head: str) -> CompareResult:
        try:
            return self.compares[(base, head)]
        except KeyError:
            raise RefNotFound(base) from None

    async def search_symbol(self, repo: RepoRef, symbol: str) -> list[SymbolHit]:
        """The same word-boundary, definition-vs-reference behaviour as
        `FixtureTransport` (`08` §3.2's implementation note) — strategy tests
        need this to be real, not stubbed, since strategy B's own logic is
        what turns a `"reference"` hit into a confirmed caller."""
        pattern = re.compile(rf"\b{re.escape(symbol)}\b")
        hits: list[SymbolHit] = []
        for path, content in self.files.items():
            for number, line in enumerate(content.splitlines(), start=1):
                if not pattern.search(line):
                    continue
                stripped = line.strip()
                kind = "reference"
                for keyword, def_kind in (("def ", "function"), ("class ", "class")):
                    prefix = f"{keyword}{symbol}"
                    if stripped.startswith(prefix) and stripped[len(prefix) : len(prefix) + 1] in (
                        "",
                        "(",
                        ":",
                        " ",
                    ):
                        kind = def_kind
                        break
                hits.append(SymbolHit(path=path, line=number, symbol=symbol, kind=kind))
        hits.extend(hit for hit in self.phantom_hits if hit.symbol == symbol)
        hits.sort(key=lambda hit: (hit.path, hit.line))
        return hits

    # ── Write — unused by strategies.py, present only so this structurally
    # satisfies `GitHubGateway` for the type checker. ─────────────────────

    async def create_blob(self, repo: RepoRef, content: str) -> Sha:
        raise NotImplementedError

    async def create_tree(self, repo: RepoRef, base_tree: Sha, entries: Sequence[TreeEntry]) -> Sha:
        raise NotImplementedError

    async def create_commit(
        self, repo: RepoRef, message: str, tree: Sha, parents: Sequence[Sha], author: Actor
    ) -> Sha:
        raise NotImplementedError

    async def create_ref(self, repo: RepoRef, ref: str, sha: Sha) -> None:
        raise NotImplementedError

    async def create_pull_request(self, repo: RepoRef, pr: PullRequestDraft) -> PullRequestRef:
        raise NotImplementedError

    async def add_labels(self, repo: RepoRef, number: int, labels: Sequence[str]) -> None:
        raise NotImplementedError
