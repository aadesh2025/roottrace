"""The per-project cost circuit breaker (`06` §8.2a, B9).

**Reserves before the call, never checks-then-acts.** A breaker that reads
today's spend and then proceeds is check-then-act: with `rt:pipeline`
concurrency of 8, all eight workers can pass the check before any of them
writes a cost row, so a single investigation can overshoot the cap by its
own entire cost, and the overshoot scales with concurrency — exactly the
situation a cost cap exists to prevent. `INCRBY`/`DECRBY` make the reserve
and release atomic, the same discipline `idempotency.py`'s `SET … NX` uses
for the same reason."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from roottrace_worker.ai.errors import QuotaExhaustedError

#: `A3`: reserved before S4, reconciled to actual on every terminal path.
#: One estimate, independent of concurrency — the worst-case overshoot B9
#: bounds the whole mechanism to.
DEFAULT_RESERVATION_ESTIMATE_MICRO_USD = 420_000

#: `A3` defaults.
DEFAULT_DAILY_CAP_MICRO_USD = 5_000_000
DEFAULT_MONTHLY_CAP_MICRO_USD = 100_000_000

#: A day's reservation key outlives the day by a comfortable margin rather
#: than expiring exactly at midnight, so a reservation made at 23:59 is not
#: silently forgotten before its reconciliation lands a few seconds later.
_DAILY_KEY_TTL_SECONDS = 2 * 24 * 60 * 60
_MONTHLY_KEY_TTL_SECONDS = 32 * 24 * 60 * 60


class RedisLike(Protocol):
    async def incrby(self, name: str, amount: int) -> int: ...

    async def decrby(self, name: str, amount: int) -> int: ...

    async def expire(self, name: str, seconds: int) -> bool: ...


@dataclass(frozen=True, slots=True)
class Reservation:
    """What `reserve` hands back — `release` needs both the project and the
    exact amount reserved to reconcile correctly, so callers are not trusted
    to remember the estimate they were given a request ago."""

    project_id: str
    day_key: str
    month_key: str
    amount_micro_usd: int


def _day_key(project_id: str, *, yyyymmdd: str) -> str:
    return f"rt:cost:{project_id}:{yyyymmdd}"


def _month_key(project_id: str, *, yyyymm: str) -> str:
    return f"rt:cost:{project_id}:{yyyymm}"


async def reserve(
    redis: RedisLike,
    *,
    project_id: str,
    yyyymmdd: str,
    estimate_micro_usd: int = DEFAULT_RESERVATION_ESTIMATE_MICRO_USD,
    daily_cap_micro_usd: int = DEFAULT_DAILY_CAP_MICRO_USD,
    monthly_cap_micro_usd: int = DEFAULT_MONTHLY_CAP_MICRO_USD,
) -> Reservation:
    """`06` §8.2a's pseudocode, literally: atomic `INCRBY`, and if either cap
    is exceeded, `DECRBY` back out before raising — the reservation must
    never be left standing on a rejected request, or every subsequent
    attempt overshoots the cap it is trying to protect."""
    yyyymm = yyyymmdd[:7].replace("-", "")
    day_key = _day_key(project_id, yyyymmdd=yyyymmdd)
    month_key = _month_key(project_id, yyyymm=yyyymm)

    day_total = await redis.incrby(day_key, estimate_micro_usd)
    await redis.expire(day_key, _DAILY_KEY_TTL_SECONDS)
    month_total = await redis.incrby(month_key, estimate_micro_usd)
    await redis.expire(month_key, _MONTHLY_KEY_TTL_SECONDS)

    if day_total > daily_cap_micro_usd:
        await redis.decrby(day_key, estimate_micro_usd)
        await redis.decrby(month_key, estimate_micro_usd)
        raise QuotaExhaustedError(project_id, "daily_cap")
    if month_total > monthly_cap_micro_usd:
        await redis.decrby(day_key, estimate_micro_usd)
        await redis.decrby(month_key, estimate_micro_usd)
        raise QuotaExhaustedError(project_id, "monthly_cap")

    return Reservation(
        project_id=project_id,
        day_key=day_key,
        month_key=month_key,
        amount_micro_usd=estimate_micro_usd,
    )


async def reconcile(redis: RedisLike, reservation: Reservation, *, actual_micro_usd: int) -> None:
    """After the pipeline terminates, on *any* outcome — success, failure,
    or cancellation. `06` §8.2a: `DECRBY (<estimate> - <actual>)`, releasing
    the pessimistic margin back to the project's remaining budget."""
    delta = reservation.amount_micro_usd - actual_micro_usd
    if delta == 0:
        return
    await redis.decrby(reservation.day_key, delta)
    await redis.decrby(reservation.month_key, delta)
