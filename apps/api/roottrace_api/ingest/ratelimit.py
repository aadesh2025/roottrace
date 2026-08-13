"""Per-key rate limiting (`05` §4, `03` §S1 step 2).

A sliding-window token bucket in Redis, keyed by API key. Two limits per plan:
requests per minute and events per minute, because a client can exhaust either
— a hundred single-event requests and one hundred-event request cost very
different things.

Evaluated in a single round trip with a Lua script so the read and the
decrement cannot interleave. The same reasoning as B7: a check followed by a
write is not a limit, it is a suggestion, and under concurrency it is exceeded
by exactly the number of workers you have.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

#: `05` §4, free plan. Per-plan limits arrive with billing; V1 is one tier.
DEFAULT_REQUESTS_PER_MINUTE = 60
DEFAULT_EVENTS_PER_MINUTE = 1_000

WINDOW_SECONDS = 60

#: Atomic consume-or-refuse. Returns the remaining allowance, or -1 when the
#: request does not fit.
#:
#: `INCRBY` then compare would let two concurrent requests both pass a check
#: they should not, so the comparison happens inside the script — the only
#: place Redis guarantees nothing runs between the two.
_CONSUME = """
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
local cost    = tonumber(ARGV[1])
local limit   = tonumber(ARGV[2])
local ttl     = tonumber(ARGV[3])

if current + cost > limit then
  return -1
end

local updated = redis.call('INCRBY', KEYS[1], cost)
if updated == cost then
  redis.call('EXPIRE', KEYS[1], ttl)
end
return limit - updated
"""


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    scope: str

    def headers(self) -> dict[str, str]:
        """`05` §4 — on **every** response, not only on a 429.

        A client that can only discover the limit by hitting it has to hit it,
        which is precisely the traffic the limit exists to prevent.
        """
        return {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(self.reset_seconds),
            "X-RateLimit-Scope": self.scope,
        }


class RedisLike(Protocol):
    async def eval(self, script: str, numkeys: int, *args: Any) -> Any: ...
    async def ttl(self, name: str) -> int: ...


def bucket_key(key_id: str, dimension: str, window: int) -> str:
    return f"rt:rl:{key_id}:{dimension}:{window}"


async def consume(
    redis: RedisLike,
    key_id: str,
    *,
    events: int,
    now: float,
    requests_per_minute: int = DEFAULT_REQUESTS_PER_MINUTE,
    events_per_minute: int = DEFAULT_EVENTS_PER_MINUTE,
) -> Decision:
    """Charge one request and `events` events against the key's buckets."""
    window = int(now // WINDOW_SECONDS)
    reset_seconds = int((window + 1) * WINDOW_SECONDS - now)

    for dimension, cost, limit in (
        ("req", 1, requests_per_minute),
        ("evt", events, events_per_minute),
    ):
        name = bucket_key(key_id, dimension, window)
        remaining = int(await redis.eval(_CONSUME, 1, name, cost, limit, WINDOW_SECONDS))
        if remaining < 0:
            return Decision(
                allowed=False,
                limit=limit,
                remaining=0,
                reset_seconds=reset_seconds,
                scope="api_key",
            )
        if dimension == "req":
            request_remaining = remaining

    return Decision(
        allowed=True,
        limit=requests_per_minute,
        remaining=request_remaining,
        reset_seconds=reset_seconds,
        scope="api_key",
    )
