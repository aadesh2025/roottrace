"""GitHub access, behind one transport-independent seam (`08` §7).

The pipeline depends on `GitHubGateway` and on nothing below it. Import the
gateway and the domain types from here; import a transport only in
`factory.py`.
"""

from roottrace_worker.github.errors import (
    AlreadyExists,
    FileNotFound,
    GitHubError,
    NoDiff,
    RateLimited,
    RefNotFound,
    TransportUnavailable,
)
from roottrace_worker.github.factory import build_gateway
from roottrace_worker.github.gateway import GitHubGateway
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

__all__ = [
    "Actor",
    "AlreadyExists",
    "BlameRange",
    "Commit",
    "CompareResult",
    "FileChange",
    "FileContent",
    "FileNotFound",
    "GitHubError",
    "GitHubGateway",
    "NoDiff",
    "PullRequestDraft",
    "PullRequestRef",
    "RateLimited",
    "RefNotFound",
    "RepoRef",
    "RepoTree",
    "Sha",
    "SymbolHit",
    "TransportUnavailable",
    "TreeEntry",
    "build_gateway",
]
