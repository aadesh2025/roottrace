"""The seam (`08` §7.1).

S5 `retrieve`, S12 `publish` and S13 `await_decision` depend on this Protocol
and on nothing below it. A `Protocol` rather than an ABC on purpose: a
transport satisfies it structurally, so a test double is a real implementation
of the contract rather than a subclass that inherits its way past the parts it
did not implement.

**No pipeline stage may contain a fixture-mode branch.** `RT_GITHUB_MODE` is
read in exactly one place, `factory.py`, and `test_transport_parity.py` fails
the build if anything else branches on it — parity that depends on discipline
will not survive ten weeks (`08` §7.1).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

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


@runtime_checkable
class GitHubGateway(Protocol):
    """Everything the pipeline may ask of GitHub."""

    # ── Read ───────────────────────────────────────────────────────────

    async def fetch_tree(self, repo: RepoRef, ref: str) -> RepoTree: ...

    async def fetch_file(self, repo: RepoRef, path: str, ref: str) -> FileContent: ...

    async def fetch_files(
        self, repo: RepoRef, paths: Sequence[str], ref: str
    ) -> list[FileContent]: ...

    async def blame(
        self, repo: RepoRef, path: str, ref: str, line_range: tuple[int, int]
    ) -> list[BlameRange]: ...

    async def recent_commits(self, repo: RepoRef, path: str, limit: int = 10) -> list[Commit]: ...

    async def compare(self, repo: RepoRef, base: str, head: str) -> CompareResult: ...

    async def search_symbol(self, repo: RepoRef, symbol: str) -> list[SymbolHit]: ...

    # ── Write ──────────────────────────────────────────────────────────

    async def create_blob(self, repo: RepoRef, content: str) -> Sha: ...

    async def create_tree(
        self, repo: RepoRef, base_tree: Sha, entries: Sequence[TreeEntry]
    ) -> Sha: ...

    async def create_commit(
        self,
        repo: RepoRef,
        message: str,
        tree: Sha,
        parents: Sequence[Sha],
        author: Actor,
    ) -> Sha: ...

    async def create_ref(self, repo: RepoRef, ref: str, sha: Sha) -> None: ...

    async def create_pull_request(self, repo: RepoRef, pr: PullRequestDraft) -> PullRequestRef: ...

    async def add_labels(self, repo: RepoRef, number: int, labels: Sequence[str]) -> None: ...
