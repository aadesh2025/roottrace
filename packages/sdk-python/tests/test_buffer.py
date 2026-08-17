"""The bounded local buffer (T2.5, `05` §10).

> drops to a bounded local buffer (1,000 events) if the API is unreachable
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from roottrace_sdk._buffer import EventBuffer

pytestmark = pytest.mark.unit


def event(index: int) -> dict[str, Any]:
    return {"event_id": f"evt_{index:04d}"}


def ids(events: list[dict[str, Any]]) -> list[str]:
    return [str(item["event_id"]) for item in events]


def test_it_is_bounded_at_its_capacity() -> None:
    buffer = EventBuffer(3)
    for index in range(10):
        buffer.add(event(index))
    assert len(buffer) == 3


def test_overflow_drops_the_newest_and_keeps_the_origin() -> None:
    """The choice is not symmetric.

    A buffer fills during an incident, and in an incident the events are
    overwhelmingly repetitions of one failure. The first occurrences carry the
    origin — the breadcrumb naming the dependency that went down before
    anything else did. The thousandth copy of the same `TypeError` carries
    nothing the first did not.
    """
    buffer = EventBuffer(3)
    for index in range(6):
        buffer.add(event(index))

    assert ids(buffer.drain()) == ["evt_0000", "evt_0001", "evt_0002"]


def test_add_reports_the_drop() -> None:
    buffer = EventBuffer(2)
    assert buffer.add(event(0)) is True
    assert buffer.add(event(1)) is True
    assert buffer.add(event(2)) is False


def test_the_dropped_count_is_kept() -> None:
    """A buffer that silently discards is indistinguishable from a buffer that
    was never full."""
    buffer = EventBuffer(2)
    for index in range(7):
        buffer.add(event(index))
    assert buffer.dropped == 5


def test_take_returns_oldest_first_and_removes_them() -> None:
    buffer = EventBuffer(10)
    for index in range(5):
        buffer.add(event(index))

    assert ids(buffer.take(2)) == ["evt_0000", "evt_0001"]
    assert len(buffer) == 3


def test_take_on_an_empty_buffer_is_empty_not_an_error() -> None:
    assert EventBuffer(10).take(5) == []


def test_put_back_restores_order_at_the_front() -> None:
    """A batch that failed to send must be retried before anything captured
    while it was in flight, or the dashboard's ordering inverts under load."""
    buffer = EventBuffer(10)
    buffer.add(event(9))
    buffer.put_back([event(0), event(1)])

    assert ids(buffer.drain()) == ["evt_0000", "evt_0001", "evt_0009"]


def test_put_back_beyond_capacity_drops_from_the_newest_end() -> None:
    """Same rule as `add`, applied from the other side."""
    buffer = EventBuffer(3)
    for index in range(3):
        buffer.add(event(100 + index))
    buffer.put_back([event(0), event(1)])

    assert ids(buffer.drain()) == ["evt_0000", "evt_0001", "evt_0100"]
    assert buffer.dropped == 2


def test_put_back_of_nothing_is_a_no_op() -> None:
    buffer = EventBuffer(3)
    buffer.add(event(0))
    buffer.put_back([])
    assert len(buffer) == 1


# ── Concurrency ────────────────────────────────────────────────────────────


def test_concurrent_producers_lose_nothing_and_double_count_nothing() -> None:
    """Producers are the host application's request threads. A `deque` without
    the lock would still pass every test above — this is the one that fails.
    """
    buffer = EventBuffer(10_000)
    ready = threading.Barrier(8)

    def produce(worker: int) -> None:
        ready.wait()
        for index in range(500):
            buffer.add(event(worker * 1000 + index))

    threads = [threading.Thread(target=produce, args=(worker,)) for worker in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    drained = buffer.drain()
    assert len(drained) == 4000
    assert len(set(ids(drained))) == 4000
    assert buffer.dropped == 0


def test_a_producer_and_the_consumer_can_run_together() -> None:
    buffer = EventBuffer(10_000)
    taken: list[dict[str, Any]] = []
    done = threading.Event()

    def consume() -> None:
        while not done.is_set() or len(buffer) > 0:
            taken.extend(buffer.take(17))

    consumer = threading.Thread(target=consume)
    consumer.start()
    for index in range(2000):
        buffer.add(event(index))
    done.set()
    consumer.join(5.0)

    assert len(taken) == 2000
    assert len(set(ids(taken))) == 2000
