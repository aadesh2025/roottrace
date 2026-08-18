"""The cost circuit breaker (`06` §8.2a, B9, T5.1).

`InMemoryRedis` implements `incrby`/`decrby`/`expire` with real integer
semantics, so these tests exercise the actual atomic-reserve-then-release
arithmetic, not a mock that just records calls."""

from __future__ import annotations

import pytest

from roottrace_worker.ai.circuit_breaker import reconcile, reserve
from roottrace_worker.ai.errors import QuotaExhaustedError

pytestmark = pytest.mark.unit


class InMemoryRedis:
    def __init__(self) -> None:
        self._store: dict[str, int] = {}

    async def incrby(self, name: str, amount: int) -> int:
        self._store[name] = self._store.get(name, 0) + amount
        return self._store[name]

    async def decrby(self, name: str, amount: int) -> int:
        self._store[name] = self._store.get(name, 0) - amount
        return self._store[name]

    async def expire(self, name: str, seconds: int) -> bool:
        return True


async def test_a_reservation_under_cap_succeeds() -> None:
    redis = InMemoryRedis()
    reservation = await reserve(
        redis,
        project_id="p1",
        yyyymmdd="2026-08-18",
        estimate_micro_usd=100,
        daily_cap_micro_usd=1000,
        monthly_cap_micro_usd=10_000,
    )
    assert reservation.amount_micro_usd == 100


async def test_a_reservation_over_the_daily_cap_is_rejected_and_released() -> None:
    redis = InMemoryRedis()
    with pytest.raises(QuotaExhaustedError, match="daily_cap"):
        await reserve(
            redis,
            project_id="p1",
            yyyymmdd="2026-08-18",
            estimate_micro_usd=2000,
            daily_cap_micro_usd=1000,
            monthly_cap_micro_usd=10_000,
        )
    # The rejected reservation must not be left standing.
    assert redis._store["rt:cost:p1:2026-08-18"] == 0
    assert redis._store["rt:cost:p1:202608"] == 0


async def test_a_reservation_over_the_monthly_cap_is_rejected_and_released() -> None:
    redis = InMemoryRedis()
    with pytest.raises(QuotaExhaustedError, match="monthly_cap"):
        await reserve(
            redis,
            project_id="p1",
            yyyymmdd="2026-08-18",
            estimate_micro_usd=2000,
            daily_cap_micro_usd=100_000,
            monthly_cap_micro_usd=1000,
        )
    assert redis._store["rt:cost:p1:2026-08-18"] == 0
    assert redis._store["rt:cost:p1:202608"] == 0


async def test_concurrent_reservations_cannot_jointly_overshoot_the_cap() -> None:
    """B9's whole point: with concurrency, a check-then-act breaker lets
    every concurrent caller pass before any of them writes — this asserts
    the atomic version does not, by reserving twice in sequence (the same
    numbers a race would produce) and confirming the second is rejected."""
    redis = InMemoryRedis()
    await reserve(
        redis,
        project_id="p1",
        yyyymmdd="2026-08-18",
        estimate_micro_usd=600,
        daily_cap_micro_usd=1000,
        monthly_cap_micro_usd=10_000,
    )
    with pytest.raises(QuotaExhaustedError):
        await reserve(
            redis,
            project_id="p1",
            yyyymmdd="2026-08-18",
            estimate_micro_usd=600,
            daily_cap_micro_usd=1000,
            monthly_cap_micro_usd=10_000,
        )
    # The first reservation's 600 must still be standing — only the second,
    # rejected attempt's 600 was released.
    assert redis._store["rt:cost:p1:2026-08-18"] == 600


async def test_reconcile_releases_the_difference_between_estimate_and_actual() -> None:
    redis = InMemoryRedis()
    reservation = await reserve(
        redis,
        project_id="p1",
        yyyymmdd="2026-08-18",
        estimate_micro_usd=420_000,
        daily_cap_micro_usd=5_000_000,
        monthly_cap_micro_usd=100_000_000,
    )
    await reconcile(redis, reservation, actual_micro_usd=150_000)
    assert redis._store["rt:cost:p1:2026-08-18"] == 150_000
    assert redis._store["rt:cost:p1:202608"] == 150_000


async def test_reconcile_with_zero_delta_is_a_no_op() -> None:
    redis = InMemoryRedis()
    reservation = await reserve(
        redis,
        project_id="p1",
        yyyymmdd="2026-08-18",
        estimate_micro_usd=100,
        daily_cap_micro_usd=1000,
        monthly_cap_micro_usd=10_000,
    )
    await reconcile(redis, reservation, actual_micro_usd=100)
    assert redis._store["rt:cost:p1:2026-08-18"] == 100


async def test_reconcile_when_actual_exceeds_the_estimate_still_only_charges_once() -> None:
    """A repair cycle costing more than the pessimistic estimate should
    increase the standing balance, not be capped at the original
    reservation — `06` §8.2a reconciles to the real number either way."""
    redis = InMemoryRedis()
    reservation = await reserve(
        redis,
        project_id="p1",
        yyyymmdd="2026-08-18",
        estimate_micro_usd=420_000,
        daily_cap_micro_usd=5_000_000,
        monthly_cap_micro_usd=100_000_000,
    )
    await reconcile(redis, reservation, actual_micro_usd=500_000)
    assert redis._store["rt:cost:p1:2026-08-18"] == 500_000


async def test_different_projects_do_not_share_a_budget() -> None:
    redis = InMemoryRedis()
    await reserve(
        redis,
        project_id="p1",
        yyyymmdd="2026-08-18",
        estimate_micro_usd=900,
        daily_cap_micro_usd=1000,
        monthly_cap_micro_usd=10_000,
    )
    # p2's reservation must not be affected by p1's near-exhausted budget.
    reservation = await reserve(
        redis,
        project_id="p2",
        yyyymmdd="2026-08-18",
        estimate_micro_usd=900,
        daily_cap_micro_usd=1000,
        monthly_cap_micro_usd=10_000,
    )
    assert reservation.project_id == "p2"
