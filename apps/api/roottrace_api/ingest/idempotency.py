"""Idempotency, claimed atomically (`03` §S1 step 3, B7).

> **B7 — why the claim must be atomic.** A plain "read the key, and if absent
> proceed" is check-then-act: two concurrent retries of the same batch both
> observe an absent key, both pass, and both insert. The result is duplicated
> `raw_events`, an inflated `occurrence_count`, and — if the duplicate crosses
> the S3 gate — a second paid pipeline run.

So there is no `get` followed by a `set` anywhere in this module. `SET … NX`
collapses the check and the claim into one operation, which is the only
formulation that is safe under concurrency.

Three outcomes, and the caller must handle all three:

- **claimed**    we own this request; proceed.
- **in flight**  a concurrent duplicate holds the claim → `409` `RT-CONFLICT-0004`.
- **replay**     a stored response → return it verbatim, re-inserting nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

#: `05` §1: 24-hour replay window.
CLAIM_TTL_SECONDS = 24 * 60 * 60

IN_FLIGHT = "in_flight"


class Outcome(Enum):
    CLAIMED = "claimed"
    IN_FLIGHT = "in_flight"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class ClaimResult:
    outcome: Outcome
    response: dict[str, Any] | None = None


class RedisLike(Protocol):
    """The three operations this needs, so a test can supply its own.

    Typed structurally rather than against a client class: the module depends
    on the semantics of `SET NX`, not on a library.
    """

    async def set(
        self, name: str, value: str, *, nx: bool = ..., ex: int | None = ...
    ) -> bool | None: ...

    async def get(self, name: str) -> str | bytes | None: ...

    async def delete(self, *names: str) -> int: ...


def claim_key(project_id: str, idempotency_key: str) -> str:
    """Namespaced per project.

    Two tenants that happen to generate the same UUID must not collide, and a
    client must not be able to probe another project's keys by guessing one.
    """
    return f"rt:idem:{project_id}:{idempotency_key}"


async def claim(redis: RedisLike, project_id: str, idempotency_key: str) -> ClaimResult:
    """Claim this request, or report what already holds the key."""
    name = claim_key(project_id, idempotency_key)

    # The whole safety property is in this one call. Anything that reads first
    # is check-then-act and duplicates under concurrency.
    if await redis.set(name, IN_FLIGHT, nx=True, ex=CLAIM_TTL_SECONDS):
        return ClaimResult(Outcome.CLAIMED)

    existing = await redis.get(name)
    if existing is None:
        # The claim expired between the SET and the GET. Vanishingly rare, and
        # treated as in-flight rather than retried: we cannot prove the earlier
        # request did not persist, and a 409 the client retries is cheaper than
        # a duplicate batch we cannot detect.
        return ClaimResult(Outcome.IN_FLIGHT)

    decoded = existing.decode() if isinstance(existing, bytes) else existing
    if decoded == IN_FLIGHT:
        return ClaimResult(Outcome.IN_FLIGHT)

    return ClaimResult(Outcome.REPLAY, response=json.loads(decoded))


async def complete(
    redis: RedisLike, project_id: str, idempotency_key: str, response: dict[str, Any]
) -> None:
    """Store the response, releasing the in-flight claim (step 10)."""
    await redis.set(
        claim_key(project_id, idempotency_key),
        json.dumps(response, separators=(",", ":")),
        ex=CLAIM_TTL_SECONDS,
    )


async def release(redis: RedisLike, project_id: str, idempotency_key: str) -> None:
    """Drop the claim after a failure, so the client's retry can proceed.

    `03` §S1: on any failure between the claim and the response the claim is
    deleted. A crashed worker leaves it to expire at 24 h, and the client sees
    409 until then — which is correct, because we cannot prove the batch was
    not persisted.
    """
    await redis.delete(claim_key(project_id, idempotency_key))
