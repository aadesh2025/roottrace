"""Breadcrumbs (T2.5, `03` §S1).

`03` §S1 calls these "disproportionately valuable" and gives the reason: the
503 from the tax service is the root cause, the `TypeError` is the symptom, and
retrieval alone would never find the first. Two properties therefore matter —
the trail must be bounded, and it must belong to the request that failed.
"""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime

import pytest

from roottrace_sdk import _breadcrumbs

pytestmark = pytest.mark.unit

ISO_MS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def test_a_breadcrumb_has_the_documented_shape() -> None:
    _breadcrumbs.begin(25)
    _breadcrumbs.add(category="http", message="GET tax-service/rate → 503", level="warning")

    (crumb,) = _breadcrumbs.snapshot()
    assert crumb["category"] == "http"
    assert crumb["message"] == "GET tax-service/rate → 503"
    assert crumb["level"] == "warning"
    assert ISO_MS.match(crumb["ts"]), crumb["ts"]


def test_the_timestamp_is_milliseconds_and_z_not_microseconds_and_offset() -> None:
    """`05` §1 fixes the format literally: `2026-08-04T09:14:22.481Z`.
    `datetime.isoformat()` produces `+00:00` and six fractional digits, and the
    corpus compares these strings, not parsed instants."""
    _breadcrumbs.begin(5)
    _breadcrumbs.add(
        category="db",
        message="SELECT",
        ts=datetime(2026, 8, 4, 9, 14, 22, 481_999, tzinfo=UTC),
    )
    assert _breadcrumbs.snapshot()[0]["ts"] == "2026-08-04T09:14:22.481Z"


def test_the_trail_is_bounded_and_keeps_the_most_recent() -> None:
    """The contract is "the last N events before the error", so overflow drops
    the oldest — the opposite end from the event buffer, and for the opposite
    reason: here the crumbs adjacent to the failure are the useful ones."""
    _breadcrumbs.begin(3)
    for index in range(10):
        _breadcrumbs.add(category="db", message=f"query {index}")

    messages = [crumb["message"] for crumb in _breadcrumbs.snapshot()]
    assert messages == ["query 7", "query 8", "query 9"]


def test_zero_breadcrumbs_records_nothing() -> None:
    _breadcrumbs.begin(0)
    _breadcrumbs.add(category="db", message="query")
    assert _breadcrumbs.snapshot() == []


def test_an_unknown_level_falls_back_rather_than_failing() -> None:
    """A breadcrumb is auxiliary. Rejecting the whole event because someone
    passed `level="critical"` would trade the error report for a style rule."""
    _breadcrumbs.begin(5)
    _breadcrumbs.add(category="db", message="query", level="critical")
    assert _breadcrumbs.snapshot()[0]["level"] == "info"


def test_a_trail_starts_itself_outside_a_request() -> None:
    """A cron job or a worker has no middleware to call `begin`."""
    _breadcrumbs.clear()
    _breadcrumbs.add(category="job", message="started")
    assert len(_breadcrumbs.snapshot()) == 1


def test_snapshot_is_a_copy() -> None:
    _breadcrumbs.begin(5)
    _breadcrumbs.add(category="db", message="query")
    taken = _breadcrumbs.snapshot()
    taken.append({"category": "forged"})
    assert len(_breadcrumbs.snapshot()) == 1


# ── The property that matters most ─────────────────────────────────────────


def test_concurrent_tasks_do_not_share_a_trail() -> None:
    """The failure this prevents is worse than having no breadcrumbs.

    With a module-level list, two requests in flight in the same thread
    interleave, and the report for request A names a database call made on
    behalf of request B — confidently wrong rather than merely absent.

    A barrier makes the interleaving deterministic: neither task can finish
    recording until both have started, so a shared list is guaranteed to be
    caught rather than caught most of the time.
    """

    async def request(name: str, barrier: asyncio.Barrier) -> list[str]:
        _breadcrumbs.begin(25)
        _breadcrumbs.add(category="http", message=f"{name}-first")
        await barrier.wait()
        _breadcrumbs.add(category="http", message=f"{name}-second")
        return [crumb["message"] for crumb in _breadcrumbs.snapshot()]

    async def both() -> tuple[list[str], list[str]]:
        barrier = asyncio.Barrier(2)
        return await asyncio.gather(request("a", barrier), request("b", barrier))

    first, second = asyncio.run(both())

    assert first == ["a-first", "a-second"]
    assert second == ["b-first", "b-second"]


def test_begin_replaces_the_deque_rather_than_clearing_it() -> None:
    """Isolation comes from the `set` landing in the task's own copy of the
    context. `clear()` would mutate the object every copy still references, so
    a new request would wipe a concurrent one's trail."""
    _breadcrumbs.begin(5)
    first = _breadcrumbs._trail.get()

    _breadcrumbs.begin(5)
    second = _breadcrumbs._trail.get()

    assert first is not None and second is not None
    assert first is not second, "begin() reused the deque; concurrent trails would collide"
