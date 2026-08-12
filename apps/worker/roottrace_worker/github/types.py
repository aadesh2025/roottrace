"""Domain types for the GitHub gateway.

`08` §7.2: *every method returns a domain type, never a raw GitHub JSON
payload.* That is what keeps GitHub's response shape out of the pipeline — a
transport has to satisfy this contract, not imitate a wire format. It is also
what makes the fixture transport honest: it cannot pass by returning
GitHub-shaped dictionaries it made up.

All frozen. A stage that could mutate a `FileContent` it was handed could
change what a later stage believes it retrieved, and evidence binding (H1/H2)
compares against these values literally.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

Sha = str


@dataclass(frozen=True, slots=True)
class RepoRef:
    """A repository, as the pipeline refers to it."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @classmethod
    def parse(cls, full_name: str) -> RepoRef:
        owner, _, name = full_name.partition("/")
        if not owner or not name:
            raise ValueError(f"not an owner/name repository reference: {full_name!r}")
        return cls(owner=owner, name=name)


@dataclass(frozen=True, slots=True)
class FileContent:
    """One file at one ref.

    `ref` is carried deliberately. Reading the wrong revision is a subtle and
    expensive failure — the model reasons about code that never ran — so the
    ref that was actually read travels with the content and is shown in the
    evidence panel (`08` §3.3).
    """

    path: str
    content: str
    sha: Sha
    ref: str

    @property
    def line_count(self) -> int:
        return len(self.content.splitlines())


@dataclass(frozen=True, slots=True)
class TreeEntry:
    path: str
    sha: Sha
    #: Git's mode string. `100644` for a file, `100755` executable, `040000`
    #: a directory.
    mode: str = "100644"
    type: str = "blob"
    size: int | None = None


@dataclass(frozen=True, slots=True)
class RepoTree:
    ref: str
    sha: Sha
    entries: tuple[TreeEntry, ...]

    def paths(self) -> tuple[str, ...]:
        return tuple(entry.path for entry in self.entries)


@dataclass(frozen=True, slots=True)
class Actor:
    name: str
    email: str


@dataclass(frozen=True, slots=True)
class Commit:
    sha: Sha
    message: str
    author: Actor
    date: datetime
    files: tuple[str, ...] = ()

    @property
    def short_sha(self) -> str:
        return self.sha[:8]

    @property
    def summary(self) -> str:
        return self.message.splitlines()[0] if self.message else ""


@dataclass(frozen=True, slots=True)
class BlameRange:
    """Who last touched a span of lines, and when.

    `start_line`/`end_line` are inclusive and 1-based, matching `git blame`
    and the ranges in `.roottrace-fixture.json`.
    """

    path: str
    start_line: int
    end_line: int
    commit: Commit

    def covers(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line


@dataclass(frozen=True, slots=True)
class FileChange:
    path: str
    status: str
    additions: int = 0
    deletions: int = 0


@dataclass(frozen=True, slots=True)
class CompareResult:
    """Two refs compared.

    Release correlation is one of the strongest signals available (`08` §3.4):
    if the failing function was modified between the previous release and the
    one that errored, that is close to conclusive and costs one call.
    """

    base: str
    head: str
    commits: tuple[Commit, ...]
    files: tuple[FileChange, ...]

    def changed_paths(self) -> tuple[str, ...]:
        return tuple(change.path for change in self.files)


@dataclass(frozen=True, slots=True)
class SymbolHit:
    path: str
    line: int
    symbol: str
    kind: str


@dataclass(frozen=True, slots=True)
class PullRequestDraft:
    title: str
    body: str
    head: str
    base: str
    draft: bool = False
    labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PullRequestRef:
    """A pull request that was created.

    `is_simulated` travels with the result rather than being inferred from
    configuration. S12 persists it to `pull_request_records` (T8.1), and a row
    that cannot say whether it describes a real pull request is a row nobody
    can trust six months later.
    """

    number: int
    url: str
    head: str
    base: str
    title: str
    body: str
    is_simulated: bool
    labels: tuple[str, ...] = field(default_factory=tuple)
