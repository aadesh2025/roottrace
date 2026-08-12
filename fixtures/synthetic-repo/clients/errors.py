"""Client exception hierarchy.

Where exceptions are conventionally defined in this codebase. A patch that
introduces a typed exception belongs here — which is why `clients/errors.py` is
in `may_modify_files` for `null-prop-01`.
"""

from __future__ import annotations


class ClientError(Exception):
    """Base for every outbound-call failure."""


class UpstreamUnavailable(ClientError):
    """An upstream dependency returned 5xx or did not respond."""

    def __init__(self, service: str, status_code: int | None = None):
        self.service = service
        self.status_code = status_code
        detail = f" (status {status_code})" if status_code is not None else ""
        super().__init__(f"{service} is unavailable{detail}")


class UpstreamTimeout(ClientError):
    """An upstream dependency exceeded its deadline."""

    def __init__(self, service: str, timeout_seconds: float):
        self.service = service
        self.timeout_seconds = timeout_seconds
        super().__init__(f"{service} did not respond within {timeout_seconds}s")


class RateLimited(ClientError):
    """An upstream dependency returned 429."""

    def __init__(self, service: str, retry_after: float | None = None):
        self.service = service
        self.retry_after = retry_after
        super().__init__(f"{service} rate limited the request")
