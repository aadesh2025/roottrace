"""Pricing — 11 tests, all passing.

`apply_discount` is always called here with all three arguments, because these
tests were updated in v2.14.2 when the signature changed. The caller in
`services/checkout.py` was not, which is `regression-01`: the test suite is
green and one call site is broken.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from config.settings import Settings
from models.config import TypedRegistry
from services import pricing


@pytest.fixture(autouse=True)
def clear_cache():
    pricing.clear_rate_cache()
    yield
    pricing.clear_rate_cache()


def test_apply_discount():
    assert pricing.apply_discount(Decimal("100.00"), 10, "us-east") == Decimal("90.00")


def test_apply_discount_rounds_per_region():
    assert pricing.apply_discount(Decimal("99.99"), 33, "ap-south") == Decimal("67")


def test_apply_zero_discount_is_a_noop():
    assert pricing.apply_discount(Decimal("100.00"), 0, "us-east") == Decimal("100.00")


def test_apply_negative_discount_is_a_noop():
    assert pricing.apply_discount(Decimal("100.00"), -5, "us-east") == Decimal("100.00")


def test_unknown_region_uses_cent_rounding():
    assert pricing.apply_discount(Decimal("10.00"), 50, "mars-1") == Decimal("5.00")


def test_cache_round_trip():
    pricing.cache_rate("eu-west", Decimal("0.20"))
    assert pricing.cached_rate("eu-west") == Decimal("0.20")


def test_cache_miss_returns_none():
    assert pricing.cached_rate("eu-west") is None


def test_clear_cache():
    pricing.cache_rate("eu-west", Decimal("0.20"))
    pricing.clear_rate_cache()
    assert pricing.cached_rate("eu-west") is None


def test_total_with_rate():
    assert pricing.total_with_rate(Decimal("100.00"), Decimal("0.2")) == Decimal("120.00")


def test_tax_service_rate_builds_a_url():
    settings = Settings(
        environment="test",
        tax_service_url="http://tax.internal",
        inventory_service_url="http://inv",
        payment_service_url="http://pay",
        export_batch_size=100,
    )
    assert pricing.tax_service_rate(settings, "eu-west").endswith("region=eu-west")


def test_merge_price_book_returns_the_target():
    target: TypedRegistry[Decimal] = TypedRegistry(name="active")
    target.put("sku-1", Decimal("10.00"))
    incoming: TypedRegistry[str] = TypedRegistry(name="supplier")
    assert pricing.merge_price_book(target, incoming) is target
