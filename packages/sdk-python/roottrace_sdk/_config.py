"""SDK configuration (`05` §10).

Every default here is the one the specification names. Where a knob is not in
the specification it is because a client behaviour listed in `05` §10 needs a
number — batch size, flush interval, buffer size — and those numbers are the
ones §10 gives.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

#: `05` §10 client behaviour, verbatim: "batches up to 100 events or 5 s,
#: whichever first ... drops to a bounded local buffer (1,000 events)".
#: 100 is also the server's hard batch limit (`05` §5, `RT-INGEST-0003`), so
#: this is not merely a tuning choice — a larger value is rejected.
DEFAULT_BATCH_SIZE = 100
DEFAULT_FLUSH_INTERVAL = 5.0
DEFAULT_BUFFER_SIZE = 1000

#: `05` §10.
DEFAULT_MAX_BREADCRUMBS = 25

DEFAULT_ENDPOINT = "https://api.roottrace.ai/v1/events"
DEFAULT_TIMEOUT = 5.0

#: Exponential backoff on 5xx and 429 (`05` §10). Five attempts spans roughly
#: 0.5 + 1 + 2 + 4 = 7.5 s of backoff, after which the batch returns to the
#: buffer rather than being discarded — the API being down for eight seconds
#: must not lose events.
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BACKOFF_BASE = 0.5
DEFAULT_BACKOFF_CAP = 30.0

#: `03` §S1: the frames are the retrieval seed, and only the deepest handful
#: ever matter. Fifty is generous for a recursion-free traceback and bounds a
#: recursive one.
DEFAULT_MAX_FRAMES = 50
DEFAULT_CONTEXT_LINES = 3

#: `05` §2.1: `rt_{live|test}_{32 hex chars}`.
API_KEY_PATTERN = re.compile(r"^rt_(live|test)_[0-9a-f]{32}$")

ENV_API_KEY = "ROOTTRACE_API_KEY"
ENV_ENDPOINT = "ROOTTRACE_ENDPOINT"

VALID_ENVIRONMENTS = frozenset({"production", "staging", "development", "test"})
VALID_LEVELS = frozenset({"error", "fatal", "warning"})

#: Mirrors `apps/api/roottrace_api/ingest/sanitise.py`'s allowlist. Duplicated
#: for the same reason as `_ids.uuid7` — the SDK has no dependencies — and kept
#: honest by `tests/integration/test_sdk_contract_agreement.py`, which fails if
#: the two lists diverge.
#:
#: Applying it client-side is defence in depth, not a replacement for the
#: server pass: it means an `Authorization` header never leaves the customer's
#: process in the first place.
HEADER_ALLOWLIST = frozenset(
    {
        "accept",
        "accept-encoding",
        "content-encoding",
        "content-length",
        "content-type",
        "user-agent",
        "x-request-id",
    }
)

BeforeSend = Callable[[dict[str, Any]], dict[str, Any] | None]


class ConfigError(ValueError):
    """A configuration mistake. Raised inside `init`, never out of it."""


@dataclass(frozen=True, slots=True)
class Config:
    api_key: str
    environment: str = "production"
    service: str | None = None
    release: str | None = None
    sample_rate: float = 1.0
    before_send: BeforeSend | None = None
    max_breadcrumbs: int = DEFAULT_MAX_BREADCRUMBS
    endpoint: str = DEFAULT_ENDPOINT
    batch_size: int = DEFAULT_BATCH_SIZE
    flush_interval: float = DEFAULT_FLUSH_INTERVAL
    buffer_size: int = DEFAULT_BUFFER_SIZE
    timeout: float = DEFAULT_TIMEOUT
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    backoff_base: float = DEFAULT_BACKOFF_BASE
    backoff_cap: float = DEFAULT_BACKOFF_CAP
    max_frames: int = DEFAULT_MAX_FRAMES
    context_lines: int = DEFAULT_CONTEXT_LINES
    #: Off by default, and deliberately. `03` §S1 shows `vars` on a frame
    #: marked "// redacted", but redaction happens at ingest — by which time a
    #: password held in a plain local has already left the customer's process.
    #: Locals are the single richest source of secrets in a Python traceback
    #: and the one place the server's entropy and pattern rules are least
    #: likely to help (`hunter2` is neither high-entropy nor pattern-shaped).
    #: Opt in per project, with client-side key redaction applied.
    capture_locals: bool = False
    in_app_include: tuple[str, ...] = ()
    tags: Mapping[str, str] = field(default_factory=dict)
    debug: bool = False

    def __post_init__(self) -> None:
        if not API_KEY_PATTERN.match(self.api_key):
            # Not merely reported: a malformed key produces 401s, the transport
            # correctly refuses to retry a 4xx, and every event is discarded
            # silently. Telling the developer at init is the difference between
            # a five-minute fix and a week of believing the service is healthy.
            raise ConfigError(
                "api_key is not a RootTrace key; expected rt_live_… or rt_test_… "
                "followed by 32 hex characters"
            )
        if self.environment not in VALID_ENVIRONMENTS:
            raise ConfigError(
                f"unknown environment {self.environment!r}; expected one of "
                + ", ".join(sorted(VALID_ENVIRONMENTS))
            )
        if not 0.0 <= self.sample_rate <= 1.0:
            raise ConfigError("sample_rate must be between 0.0 and 1.0")
        if not 1 <= self.batch_size <= DEFAULT_BATCH_SIZE:
            raise ConfigError(f"batch_size must be between 1 and {DEFAULT_BATCH_SIZE}")
        if self.buffer_size < 1:
            raise ConfigError("buffer_size must be at least 1")
        if self.flush_interval <= 0:
            raise ConfigError("flush_interval must be positive")
        if self.max_breadcrumbs < 0:
            raise ConfigError("max_breadcrumbs cannot be negative")
        if self.max_attempts < 1:
            raise ConfigError("max_attempts must be at least 1")
        if self.backoff_base <= 0 or self.backoff_cap <= 0:
            raise ConfigError("backoff_base and backoff_cap must be positive")
        _check_endpoint(self.endpoint)


def _check_endpoint(endpoint: str) -> None:
    """HTTPS, or a loopback host.

    The API key travels in an `Authorization` header on every request. Allowing
    plain `http://` to an arbitrary host would put a live credential on the
    wire in cleartext, and `05` §1 fixes the transport as HTTPS only. Loopback
    is exempt so a developer can point the SDK at a local API without the
    exemption ever covering a real network hop.
    """
    parsed = urlparse(endpoint)
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
        return
    raise ConfigError(
        f"endpoint must be https (or http on loopback); got {endpoint!r}. "
        "The API key is sent on every request."
    )


def build(
    *,
    api_key: str | None = None,
    endpoint: str | None = None,
    **options: Any,
) -> Config:
    """Resolve `init`'s keyword arguments into a validated `Config`."""
    resolved_key = api_key or os.environ.get(ENV_API_KEY)
    if not resolved_key:
        raise ConfigError(f"api_key is required (or set {ENV_API_KEY})")

    resolved_endpoint = endpoint or os.environ.get(ENV_ENDPOINT) or DEFAULT_ENDPOINT

    unknown = set(options) - {f for f in Config.__dataclass_fields__ if f != "api_key"}
    if unknown:
        # A typo in a keyword is otherwise a silent no-op: `max_breadcrumb=5`
        # would leave the default in place and look like the SDK ignoring the
        # setting.
        raise ConfigError("unknown option(s): " + ", ".join(sorted(unknown)))

    return Config(api_key=resolved_key, endpoint=resolved_endpoint, **options)
