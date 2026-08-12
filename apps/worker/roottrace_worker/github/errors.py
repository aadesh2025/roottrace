"""Gateway failures.

Every transport raises these, never its own. A stage that had to catch
`httpx.HTTPStatusError` from one transport and something else from another
would be transport-aware, which is the whole thing `08` §7 forbids.

Codes are the registered ones from `17` §4 — `RT-GITHUB-0001` through
`0008`. Nothing here invents a code.
"""

from __future__ import annotations


class GitHubError(Exception):
    """Base for every gateway failure."""

    code = "RT-GITHUB-0001"


class FileNotFound(GitHubError):
    """A path does not exist at the requested ref.

    `08` GC2: raised rather than returning empty content. Empty content is
    indistinguishable from a legitimately empty file, and a stage that
    retrieved "nothing" would reason about a file that is not there.
    """

    code = "RT-GITHUB-0004"

    def __init__(self, path: str, ref: str):
        self.path = path
        self.ref = ref
        super().__init__(f"{path} does not exist at {ref}")


class RefNotFound(GitHubError):
    """A ref (branch, tag or SHA) could not be resolved."""

    code = "RT-GITHUB-0004"

    def __init__(self, ref: str):
        self.ref = ref
        super().__init__(f"ref {ref} could not be resolved")


class AlreadyExists(GitHubError):
    """A branch or ref already exists (`08` GC9)."""

    code = "RT-GITHUB-0003"

    def __init__(self, ref: str):
        self.ref = ref
        super().__init__(f"{ref} already exists")


class NoDiff(GitHubError):
    """A pull request was requested with no difference between the branches.

    `08` GC11: terminal `failed` with `RT-GITHUB-0006`. Opening an empty PR
    would put a human in front of a patch that changes nothing, which is worse
    than failing — it spends the reviewer's attention, which is the scarcest
    thing in the loop.
    """

    code = "RT-GITHUB-0006"

    def __init__(self, head: str, base: str):
        self.head = head
        self.base = base
        super().__init__(f"no difference between {base} and {head}")


class RateLimited(GitHubError):
    """`08` GC10. Carries `retry_after` so the caller can back off exactly as
    instructed rather than guessing."""

    code = "RT-GITHUB-0005"

    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__("github rate limit exhausted")


class TransportUnavailable(GitHubError):
    """The requested transport is not built.

    Distinct from a failure *inside* a transport: this says the mode itself
    cannot be served, and it names the ticket that will serve it.
    """

    code = "RT-GITHUB-0002"
