"""Breadcrumbs — the last N things that happened before the error.

`03` §S1 is unusually emphatic about these:

> these are disproportionately valuable. In the example above, the `503` from
> the tax service is the actual root cause — the `TypeError` is a downstream
> symptom. Retrieval alone would never find that; the breadcrumb does.

**The trail is a `ContextVar`, not a module global.** Under any async framework
several requests are in flight in the same thread at the same time. A shared
list would interleave their breadcrumbs, and the report for request A would
name a database call made on behalf of request B — worse than no breadcrumbs,
because it is confidently wrong.

`ContextVar` gives the isolation because each ASGI request runs in its own
task, and a task copies the context at creation. The middleware calls `begin()`
per request, and that `set` is visible only inside that request's context.
`begin` must be a *new* deque rather than a `clear()`: mutating the object
already in the context would reach every context holding a reference to it.
"""

from __future__ import annotations

from collections import deque
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from roottrace_sdk._config import DEFAULT_MAX_BREADCRUMBS
from roottrace_sdk._time import isoformat_ms

Breadcrumb = dict[str, Any]

_trail: ContextVar[deque[Breadcrumb] | None] = ContextVar("roottrace_breadcrumbs", default=None)

VALID_LEVELS = frozenset({"debug", "info", "warning", "error"})


def begin(max_breadcrumbs: int = DEFAULT_MAX_BREADCRUMBS) -> None:
    """Start a fresh trail for the current context."""
    _trail.set(deque(maxlen=max(0, max_breadcrumbs)))


def add(
    *,
    category: str,
    message: str,
    level: str = "info",
    data: dict[str, Any] | None = None,
    max_breadcrumbs: int = DEFAULT_MAX_BREADCRUMBS,
    ts: datetime | None = None,
) -> None:
    """Append to the current trail, creating one if `begin` was never called.

    The lazy creation is what makes the SDK usable outside a request — a cron
    job or a worker has no middleware to call `begin`.

    `deque(maxlen=N)` drops the *oldest* on overflow, which is the right end
    for breadcrumbs specifically: the contract is "the last N events before the
    error", and the most recent are the ones adjacent to it.
    """
    trail = _trail.get()
    if trail is None:
        trail = deque(maxlen=max(0, max_breadcrumbs))
        _trail.set(trail)
    if trail.maxlen == 0:
        return

    crumb: Breadcrumb = {
        "ts": isoformat_ms(ts or datetime.now(UTC)),
        "category": str(category),
        "message": str(message),
        "level": level if level in VALID_LEVELS else "info",
    }
    if data:
        crumb["data"] = data
    trail.append(crumb)


def snapshot() -> list[Breadcrumb]:
    """A copy, oldest first. The caller attaches it to an event."""
    trail = _trail.get()
    return list(trail) if trail else []


def clear() -> None:
    _trail.set(None)
