"""The bounded local buffer (`05` §10).

> drops to a bounded local buffer (1,000 events) if the API is unreachable

**On overflow the newest event is dropped, not the oldest.** The choice is not
symmetric and it is worth stating why. A buffer fills during an incident, and
in an incident the events are overwhelmingly repetitions of one failure. The
first occurrences are the ones that carry the origin — the breadcrumb showing
the dependency that went down before anything else did. The thousandth copy of
the same `TypeError` carries nothing the first did not.

The count of dropped events is kept and reported, because a buffer that
silently discards is indistinguishable from a buffer that was never full.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Any

Event = dict[str, Any]


class EventBuffer:
    """Thread-safe, bounded, drop-newest.

    Producers are the host application's request threads; the single consumer
    is the sender thread. The lock is held only for `deque` operations, never
    across a network call.
    """

    def __init__(self, capacity: int) -> None:
        self._capacity = max(1, capacity)
        self._events: deque[Event] = deque()
        self._lock = threading.Lock()
        self._dropped = 0

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def __len__(self) -> int:
        with self._lock:
            return len(self._events)

    def add(self, event: Event) -> bool:
        """Returns False if the buffer was full and the event was dropped."""
        with self._lock:
            if len(self._events) >= self._capacity:
                self._dropped += 1
                return False
            self._events.append(event)
            return True

    def take(self, limit: int) -> list[Event]:
        """Remove and return up to `limit` events, oldest first."""
        with self._lock:
            batch = [self._events.popleft() for _ in range(min(limit, len(self._events)))]
        return batch

    def put_back(self, events: list[Event]) -> None:
        """Return an unsent batch to the front, preserving order.

        Used when a send exhausts its retries: the API being briefly down must
        not lose the batch that was in flight. Overflow here drops from the
        *back* — the newest — for the same reason `add` does.
        """
        if not events:
            return
        with self._lock:
            self._events.extendleft(reversed(events))
            overflow = len(self._events) - self._capacity
            if overflow > 0:
                for _ in range(overflow):
                    self._events.pop()
                self._dropped += overflow

    def drain(self) -> list[Event]:
        with self._lock:
            batch = list(self._events)
            self._events.clear()
        return batch
