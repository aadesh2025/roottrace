"""Fixed-window rate limiting.

Also bug-free, and also deliberately plausible: it is in-process state keyed by
client id, which is the shape of `race-02`, so a ranker that matches on shape
rather than on evidence will surface it for the wrong case.
"""

from __future__ import annotations

import time

WINDOW_SECONDS = 60
DEFAULT_LIMIT = 120


class RateLimiter:
    def __init__(self, limit: int = DEFAULT_LIMIT):
        self.limit = limit
        self._counters: dict[str, tuple[int, float]] = {}

    def allow(self, client_id: str, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        count, window_start = self._counters.get(client_id, (0, now))
        if now - window_start >= WINDOW_SECONDS:
            self._counters[client_id] = (1, now)
            return True
        if count >= self.limit:
            return False
        self._counters[client_id] = (count + 1, window_start)
        return True

    def remaining(self, client_id: str) -> int:
        count, _ = self._counters.get(client_id, (0, 0.0))
        return max(0, self.limit - count)
