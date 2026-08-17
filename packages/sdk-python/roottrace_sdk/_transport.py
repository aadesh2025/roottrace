"""HTTP transport (`05` §5, §10).

> gzip; retries with exponential backoff on 5xx and 429

Built on `urllib.request` rather than `httpx` or `requests`. The SDK is
installed into customer applications, so every dependency it declares is a
version constraint imposed on someone else's resolver — and the one thing it
does is POST a JSON body. The standard library covers that.

**Which failures are retryable is the decision that matters.** 5xx, 429 and any
transport-level error are ours or the network's, and retrying is right. A 4xx
is the client's — a malformed batch or a revoked key — and retrying it forever
means the buffer never drains and every subsequent event is dropped behind a
batch that can never succeed. Those are dropped, once, loudly.
"""

from __future__ import annotations

import gzip
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from roottrace_sdk._config import Config
from roottrace_sdk._version import __version__

#: `05` §5: 202 on success. Anything else 2xx is accepted too rather than
#: treated as failure — a proxy that answers 200 has still taken the batch.
_SUCCESS = range(200, 300)

_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 507, 509})


@dataclass(frozen=True, slots=True)
class SendResult:
    ok: bool
    retryable: bool
    status: int | None = None
    retry_after: float | None = None
    detail: str | None = None


class HttpTransport:
    """One POST to `/v1/events`. No state, no connection pooling.

    Connection reuse would help throughput and is deliberately skipped in V1:
    a batch leaves at most once every five seconds, so the handshake cost is
    irrelevant, and a pooled connection held open across a `fork` is a class of
    bug that is very hard to see from inside a customer's application.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        # Re-validated here rather than trusted from `Config`: this is the line
        # that actually puts the credential on the wire.
        scheme = urlparse(config.endpoint).scheme
        if scheme not in {"http", "https"}:
            raise ValueError(f"unsupported endpoint scheme: {scheme!r}")

    def send(self, events: list[dict[str, Any]], *, idempotency_key: str) -> SendResult:
        body = gzip.compress(
            json.dumps({"events": events}, separators=(",", ":"), default=str).encode()
        )
        request = urllib.request.Request(  # noqa: S310 - scheme checked in __init__
            self._config.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._config.api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "Content-Encoding": "gzip",
                # The same key across every retry of the same batch — that is
                # the whole point of `03` §S1's 24 h replay window. A fresh key
                # per attempt would make a timed-out-but-persisted batch land
                # twice.
                "Idempotency-Key": idempotency_key,
                "User-Agent": f"roottrace-python/{__version__}",
                "Accept": "application/json",
            },
        )

        try:
            with urllib.request.urlopen(  # noqa: S310 - as above
                request, timeout=self._config.timeout
            ) as response:
                status = int(response.status)
                if status in _SUCCESS:
                    return SendResult(ok=True, retryable=False, status=status)
                return self._failure(status, None)
        except urllib.error.HTTPError as exc:
            return self._failure(int(exc.code), exc.headers.get("Retry-After"))
        except Exception as exc:
            # URLError, socket.timeout, DNS failure, a TLS error, a connection
            # reset mid-body. All of them mean "the API is unreachable", which
            # `05` §10 answers with the buffer, not with a lost batch.
            return SendResult(ok=False, retryable=True, detail=type(exc).__name__)

    def _failure(self, status: int, retry_after: str | None) -> SendResult:
        return SendResult(
            ok=False,
            retryable=status in _RETRYABLE_STATUSES,
            status=status,
            retry_after=_parse_retry_after(retry_after),
            detail=f"HTTP {status}",
        )


def _parse_retry_after(value: str | None) -> float | None:
    """Seconds only. The HTTP-date form is legal and we do not honour it —
    parsing a date against a possibly-skewed client clock can produce a
    negative or absurd delay, and the backoff schedule is a safe fallback."""
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def backoff_delay(attempt: int, *, base: float, cap: float, retry_after: float | None) -> float:
    """Exponential, capped, and overridden by an explicit `Retry-After`.

    `attempt` is 1-based, so the first retry waits `base`. No jitter in V1: a
    single process sends at most one batch at a time, so there is no thundering
    herd to break up — the herd would be across customer processes, and
    jittering ours would not change theirs.
    """
    if retry_after is not None:
        return min(retry_after, cap)
    return float(min(base * (2 ** (attempt - 1)), cap))
