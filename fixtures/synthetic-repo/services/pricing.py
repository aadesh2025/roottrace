"""Pricing arithmetic.

`regression-01`: `apply_discount` grew a required `region` parameter in
v2.14.2 so regional rounding rules could be applied. Every caller was updated
except `services/checkout.py::discounted_subtotal`.

`race-02`: `_RATE_CACHE` is module-level and mutable, so it is shared by every
request in the process. It is keyed by region but written without a lock and
never invalidated, so a value written while serving one customer is read while
serving another.

`null-prop-03`: `tax_service_rate` reads an optional setting and uses it
directly.

`type-mismatch-03`: `merge_price_book` merges a registry of strings into a
registry of Decimals through the unparameterised `merge` helper.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from config.settings import Settings
from models.config import TypedRegistry

# Shared across every request in the process. Written on read-miss, never
# invalidated, and not guarded by anything.
_RATE_CACHE: dict[str, Decimal] = {}

ROUNDING_BY_REGION = {
    "us-east": Decimal("0.01"),
    "us-west": Decimal("0.01"),
    "eu-west": Decimal("0.01"),
    "ap-south": Decimal("1"),
}


def apply_discount(amount: Decimal, percent: int, region: str) -> Decimal:
    """Apply a percentage discount, rounded per regional rules.

    `region` became required in v2.14.2. The old two-argument form is what
    `checkout.discounted_subtotal` still calls.
    """
    if percent <= 0:
        return amount
    discounted = amount * (Decimal(100 - percent) / Decimal(100))
    quantum = ROUNDING_BY_REGION.get(region, Decimal("0.01"))
    return discounted.quantize(quantum, rounding=ROUND_HALF_UP)


def cache_rate(region: str, rate: Decimal) -> None:
    """`race-02`: an unsynchronised write to process-global state."""
    _RATE_CACHE[region] = rate


def cached_rate(region: str) -> Decimal | None:
    return _RATE_CACHE.get(region)


def clear_rate_cache() -> None:
    _RATE_CACHE.clear()


def tax_service_rate(settings: Settings, region: str) -> str:
    """Build the tax-service URL for a region.

    `null-prop-03`: `tax_service_url` is optional — local development runs
    without a tax service — and this concatenates it without checking.
    """
    return settings.tax_service_url + "/rate?region=" + region


def merge_price_book(
    target: TypedRegistry[Decimal], incoming: TypedRegistry[str]
) -> TypedRegistry[Decimal]:
    """Merge a supplier price book into the active one.

    `type-mismatch-03`: suppliers send prices as strings. `merge` takes an
    unparameterised registry, so nothing rejects them, and the strings sit in
    a `TypedRegistry[Decimal]` until something does arithmetic on one.
    """
    target.merge(incoming)
    return target


def total_with_rate(subtotal: Decimal, rate: Decimal) -> Decimal:
    return (subtotal * (Decimal("1") + rate)).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
