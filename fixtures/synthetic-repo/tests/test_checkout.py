"""Checkout service — 12 tests, all passing.

Every one of them stubs the tax client with a real Decimal, which is why
`null-prop-01` has survived three releases: the failure path the client
actually takes in production is not exercised anywhere in here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from models.cart import Cart, CartItem
from services.checkout import MANUAL_REVIEW_THRESHOLD, CheckoutService
from tests.conftest import StubPaymentClient, StubTaxClient


@pytest.fixture
def service() -> CheckoutService:
    return CheckoutService(StubTaxClient(), StubPaymentClient())


def test_calculate_total_adds_tax(service, cart, user):
    assert service.calculate_total(cart, user) == Decimal("50.19")


def test_calculate_total_uses_the_cart_region(service, cart, user):
    service.calculate_total(cart, user)
    assert service.tax_client.calls == ["eu-west"]


def test_calculate_total_on_an_empty_cart(service, user):
    empty = Cart(id="c_empty", region="us-east")
    assert service.calculate_total(empty, user) == Decimal("0.20")


def test_create_order_carries_the_total(service, cart, user):
    order = service.create_order(cart, user)
    assert order.total == Decimal("50.19")
    assert order.cart_id == "c_8821"


def test_create_order_is_pending_by_default(service, cart, user):
    assert service.create_order(cart, user).status == "pending"


def test_large_orders_are_held_for_review(service, user):
    big = Cart(
        id="c_big",
        region="us-east",
        items=[CartItem(sku="sku-9", quantity=1, unit_price=Decimal("5000.00"))],
    )
    assert service.create_order(big, user).status == "review"


def test_needs_manual_review_at_the_threshold(service):
    assert service.needs_manual_review(MANUAL_REVIEW_THRESHOLD)


def test_needs_manual_review_below_the_threshold(service):
    assert not service.needs_manual_review(Decimal("10.00"))


def test_region_settings_for_a_known_region(service):
    assert service._region_settings("eu-west")["currency"] == "EUR"


def test_tax_inclusive_regions(service):
    assert service.is_tax_inclusive("eu-west")
    assert not service.is_tax_inclusive("us-east")


def test_capture_reference_reads_the_receipt_id(service):
    assert service.capture_reference({"id": "ch_1", "amount": "1.00"}) == "ch_1"


def test_capture_reference_rejects_a_receipt_without_an_id(service):
    with pytest.raises(KeyError):
        service.capture_reference({"amount": "1.00"})
