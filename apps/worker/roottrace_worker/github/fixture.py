"""The fixture transport (`08` §7.3).

Reads come from `fixtures/synthetic-repo/` and its `.roottrace-fixture.json`;
writes are recorded in memory and returned as simulated results for S12 to
persist (T8.1). **No network call to GitHub occurs at all** — there is no HTTP
client in this module, so the claim is structural rather than a promise.

Object ids are computed with **real git object hashing**, not invented. GC1
requires `fetch_file` to return a byte-identical `sha` across transports, and
GitHub returns the git blob id; a transport that made up plausible hex would
satisfy every test we can write today and diverge the moment `live` exists.
The same goes for trees and commits — the encodings are git's, so the ids are
the ones `git hash-object` produces.

**One honest limitation.** The fixture tree has a single revision on disk, so
`ref` is resolved and validated and recorded, but it does not select content:
asking for `v2.14.1` returns today's bytes. Nothing in V1 reads a historical
ref (retrieval reads the release that errored, which is HEAD here), and
`compare` and `blame` come from the simulated history, which does distinguish
revisions. It is stated rather than hidden because a transport that silently
returned the wrong revision would be the exact failure `08` §3.3 warns about.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from roottrace_worker.github.errors import (
    AlreadyExists,
    FileNotFound,
    NoDiff,
    RefNotFound,
)
from roottrace_worker.github.types import (
    Actor,
    BlameRange,
    Commit,
    CompareResult,
    FileChange,
    FileContent,
    PullRequestDraft,
    PullRequestRef,
    RepoRef,
    RepoTree,
    Sha,
    SymbolHit,
    TreeEntry,
)

METADATA_FILE = ".roottrace-fixture.json"

#: Never returned by `fetch_tree` and never walked. Build artefacts and the
#: simulated history are not part of the repository under analysis.
IGNORED_DIRECTORIES = frozenset({"__pycache__", ".git", ".history", ".pytest_cache"})


def _git_object_id(kind: str, payload: bytes) -> Sha:
    """The real git object id: sha1 of `<kind> <len>\\0<payload>`."""
    header = f"{kind} {len(payload)}".encode() + b"\0"
    return hashlib.sha1(header + payload, usedforsecurity=False).hexdigest()


def blob_sha(content: str) -> Sha:
    """`git hash-object` for a blob. What GitHub reports as the file's sha."""
    return _git_object_id("blob", content.encode())


def _tree_sort_key(name: str, is_directory: bool) -> bytes:
    # Git sorts tree entries by name, treating a directory as though it ended
    # in "/". Getting this wrong produces a plausible id that no git
    # implementation agrees with.
    return (name + "/").encode() if is_directory else name.encode()


class FixtureTransport:
    """A `GitHubGateway` backed by the local fixture tree."""

    def __init__(self, root: Path | str, *, simulated_pr_base_number: int = 1):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise FileNotFoundError(f"fixture repository not found at {self.root}")

        self._metadata: dict[str, Any] = json.loads(
            (self.root / METADATA_FILE).read_text(encoding="utf-8")
        )
        self._next_pr_number = simulated_pr_base_number

        # Written objects. Held here rather than on disk so a run cannot
        # mutate the fixture repository the whole corpus is measured against.
        self.blobs: dict[Sha, str] = {}
        self.trees: dict[Sha, tuple[TreeEntry, ...]] = {}
        self.commits: dict[Sha, dict[str, Any]] = {}
        self.refs: dict[str, Sha] = {}
        self.pull_requests: list[PullRequestRef] = []

    # ── Metadata ───────────────────────────────────────────────────────

    @property
    def default_branch(self) -> str:
        return str(self._metadata["default_branch"])

    @property
    def head_sha(self) -> Sha:
        return str(self._metadata["head_sha"])

    def _history(self) -> list[Commit]:
        """The simulated history, oldest first."""
        return [self._commit_from(entry) for entry in self._metadata["commits"]]

    @staticmethod
    def _commit_from(entry: dict[str, Any]) -> Commit:
        author = entry.get("author", {})
        return Commit(
            sha=str(entry["sha"]),
            message=str(entry.get("message", "")),
            author=Actor(
                name=str(author.get("name", "unknown")),
                email=str(author.get("email", "unknown@example.test")),
            ),
            date=datetime.fromisoformat(str(entry["date"]).replace("Z", "+00:00")),
            files=tuple(entry.get("files", ())),
        )

    def resolve_ref(self, ref: str) -> Sha:
        """Resolve a tag, branch, full or short SHA to a commit sha.

        GC12 pins the priority: a release tag first, then the default branch,
        then a SHA. Reading the wrong revision is expensive and quiet, so an
        unresolvable ref raises rather than falling back to HEAD.
        """
        for release in self._metadata.get("releases", []):
            if release["tag"] == ref:
                return str(release["sha"])

        if ref in (self.default_branch, "HEAD", f"refs/heads/{self.default_branch}"):
            return self.head_sha

        if ref in self.refs:
            return self.refs[ref]

        known = {entry["sha"] for entry in self._metadata["commits"]} | set(self.commits)
        for sha in known:
            if sha == ref or (sha.startswith(ref) and len(ref) >= 7):
                return str(sha)

        raise RefNotFound(ref)

    # ── Paths ──────────────────────────────────────────────────────────

    def _resolve_path(self, path: str) -> Path:
        candidate = (self.root / path).resolve()
        # A fixture case, a model-generated patch or a malformed frame path
        # could all point outside the tree. Refused rather than trusted:
        # everything reaching this layer is untrusted input (CLAUDE.md).
        if not candidate.is_relative_to(self.root):
            raise FileNotFound(path, "<escapes the repository root>")
        return candidate

    def _walk(self) -> Iterable[Path]:
        for candidate in sorted(self.root.rglob("*")):
            if not candidate.is_file():
                continue
            if any(part in IGNORED_DIRECTORIES for part in candidate.relative_to(self.root).parts):
                continue
            if candidate.name == METADATA_FILE:
                continue
            yield candidate

    # ── Read ───────────────────────────────────────────────────────────

    async def fetch_tree(self, repo: RepoRef, ref: str) -> RepoTree:
        self.resolve_ref(ref)
        entries: list[TreeEntry] = []
        for candidate in self._walk():
            relative = candidate.relative_to(self.root).as_posix()
            content = candidate.read_text(encoding="utf-8")
            entries.append(
                TreeEntry(
                    path=relative,
                    sha=blob_sha(content),
                    mode="100644",
                    type="blob",
                    size=len(content.encode()),
                )
            )
        entries.sort(key=lambda entry: entry.path)
        return RepoTree(ref=ref, sha=self._root_tree_sha(entries), entries=tuple(entries))

    def _root_tree_sha(self, entries: Sequence[TreeEntry]) -> Sha:
        """The real recursive tree id, built bottom-up as git builds it."""
        nested: dict[str, Any] = {}
        for entry in entries:
            cursor = nested
            parts = entry.path.split("/")
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = entry

        def write(node: dict[str, Any]) -> Sha:
            payload = bytearray()
            for name in sorted(node, key=lambda n: _tree_sort_key(n, isinstance(node[n], dict))):
                child = node[name]
                if isinstance(child, dict):
                    mode, sha = "40000", write(child)
                else:
                    mode, sha = child.mode.lstrip("0") or "0", child.sha
                    mode = child.mode
                payload += f"{mode} {name}".encode() + b"\0" + bytes.fromhex(sha)
            return _git_object_id("tree", bytes(payload))

        return write(nested)

    async def fetch_file(self, repo: RepoRef, path: str, ref: str) -> FileContent:
        self.resolve_ref(ref)
        candidate = self._resolve_path(path)
        if not candidate.is_file():
            # GC2: never an empty FileContent. Empty content is
            # indistinguishable from a legitimately empty file.
            raise FileNotFound(path, ref)

        content = candidate.read_text(encoding="utf-8")
        return FileContent(path=path, content=content, sha=blob_sha(content), ref=ref)

    async def fetch_files(self, repo: RepoRef, paths: Sequence[str], ref: str) -> list[FileContent]:
        # GC4: identical to N fetch_file calls, order preserved. Deliberately not
        # deduplicated or reordered — a caller that asked for a path twice
        # gets it twice, and retrieval's ordering carries its ranking.
        return [await self.fetch_file(repo, path, ref) for path in paths]

    async def blame(
        self, repo: RepoRef, path: str, ref: str, line_range: tuple[int, int]
    ) -> list[BlameRange]:
        self.resolve_ref(ref)
        low, high = line_range
        by_sha = {commit.sha[:8]: commit for commit in self._history()}

        ranges: list[BlameRange] = []
        for entry in self._metadata.get("blame", {}).get(path, []):
            start, end = entry["lines"]
            if end < low or start > high:
                continue
            commit = by_sha.get(str(entry["sha"])[:8])
            if commit is None:
                continue
            ranges.append(BlameRange(path=path, start_line=start, end_line=end, commit=commit))
        ranges.sort(key=lambda item: item.start_line)
        return ranges

    async def recent_commits(self, repo: RepoRef, path: str, limit: int = 10) -> list[Commit]:
        touching = [commit for commit in self._history() if path in commit.files]
        touching.sort(key=lambda commit: commit.date, reverse=True)
        return touching[:limit]

    async def compare(self, repo: RepoRef, base: str, head: str) -> CompareResult:
        base_sha, head_sha = self.resolve_ref(base), self.resolve_ref(head)
        history = self._history()
        order = {commit.sha: index for index, commit in enumerate(history)}
        if base_sha not in order or head_sha not in order:
            raise RefNotFound(base if base_sha not in order else head)

        low, high = order[base_sha], order[head_sha]
        if low > high:
            low, high = high, low
        # Exclusive of the base, as `base...head` is.
        between = history[low + 1 : high + 1]

        changed: dict[str, FileChange] = {}
        for commit in between:
            for path in commit.files:
                changed.setdefault(path, FileChange(path=path, status="modified"))

        return CompareResult(
            base=base,
            head=head,
            commits=tuple(between),
            files=tuple(changed[path] for path in sorted(changed)),
        )

    async def search_symbol(self, repo: RepoRef, symbol: str) -> list[SymbolHit]:
        hits: list[SymbolHit] = []
        for candidate in self._walk():
            if candidate.suffix != ".py":
                continue
            relative = candidate.relative_to(self.root).as_posix()
            for number, line in enumerate(
                candidate.read_text(encoding="utf-8").splitlines(), start=1
            ):
                stripped = line.strip()
                for keyword, kind in (("def ", "function"), ("class ", "class")):
                    prefix = f"{keyword}{symbol}"
                    if stripped.startswith(prefix) and _is_definition(stripped, prefix):
                        hits.append(SymbolHit(path=relative, line=number, symbol=symbol, kind=kind))
        hits.sort(key=lambda hit: (hit.path, hit.line))
        return hits

    # ── Write ──────────────────────────────────────────────────────────

    async def create_blob(self, repo: RepoRef, content: str) -> Sha:
        sha = blob_sha(content)
        self.blobs[sha] = content
        return sha

    async def create_tree(self, repo: RepoRef, base_tree: Sha, entries: Sequence[TreeEntry]) -> Sha:
        merged = {entry.path: entry for entry in self.trees.get(base_tree, ())}
        merged.update({entry.path: entry for entry in entries})
        ordered = tuple(merged[path] for path in sorted(merged))
        sha = self._root_tree_sha(ordered)
        self.trees[sha] = ordered
        return sha

    async def create_commit(
        self,
        repo: RepoRef,
        message: str,
        tree: Sha,
        parents: Sequence[Sha],
        author: Actor,
    ) -> Sha:
        # Git's commit object, so the id is the one git would compute. The
        # timestamp is fixed: a wall-clock commit id would make the same
        # patch produce a different sha on every run, and the eval harness
        # compares runs.
        stamp = "1754301600 +0000"
        lines = [f"tree {tree}"]
        lines += [f"parent {parent}" for parent in parents]
        lines.append(f"author {author.name} <{author.email}> {stamp}")
        lines.append(f"committer {author.name} <{author.email}> {stamp}")
        payload = ("\n".join(lines) + "\n\n" + message + "\n").encode()

        sha = _git_object_id("commit", payload)
        self.commits[sha] = {
            "message": message,
            "tree": tree,
            "parents": list(parents),
            "author": author,
        }
        return sha

    async def create_ref(self, repo: RepoRef, ref: str, sha: Sha) -> None:
        # GC9: a collision raises rather than overwriting. Silently moving a
        # branch someone else created is how a patch lands on top of another.
        if ref in self.refs:
            raise AlreadyExists(ref)
        self.refs[ref] = sha

    async def create_pull_request(self, repo: RepoRef, pr: PullRequestDraft) -> PullRequestRef:
        # GC11: an empty diff is refused. Opening a PR that changes nothing
        # spends a reviewer's attention, which is the scarcest thing in the
        # loop.
        if pr.head == pr.base:
            raise NoDiff(pr.head, pr.base)
        if pr.head not in self.refs and f"refs/heads/{pr.head}" not in self.refs:
            raise RefNotFound(pr.head)

        number = self._next_pr_number
        self._next_pr_number += 1

        record = PullRequestRef(
            number=number,
            # The URL a real PR would have. S12 stores it and the dashboard
            # displays it, so fixture output is reviewable as the real
            # artefact (`08` §7.5).
            url=f"https://github.com/{repo.full_name}/pull/{number}",
            head=pr.head,
            base=pr.base,
            title=pr.title,
            body=pr.body,
            is_simulated=True,
            labels=tuple(pr.labels),
        )
        self.pull_requests.append(record)
        return record

    async def add_labels(self, repo: RepoRef, number: int, labels: Sequence[str]) -> None:
        for index, record in enumerate(self.pull_requests):
            if record.number == number:
                merged = tuple(dict.fromkeys([*record.labels, *labels]))
                self.pull_requests[index] = PullRequestRef(
                    number=record.number,
                    url=record.url,
                    head=record.head,
                    base=record.base,
                    title=record.title,
                    body=record.body,
                    is_simulated=record.is_simulated,
                    labels=merged,
                )
                return
        raise RefNotFound(f"pull request #{number}")


def _is_definition(stripped: str, prefix: str) -> bool:
    """Reject `def calculate_total_v2` when searching for `calculate_total`."""
    remainder = stripped[len(prefix) :]
    return remainder[:1] in ("", "(", ":", " ")
