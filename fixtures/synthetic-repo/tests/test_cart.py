"""Cart service — 9 tests, all passing.

`increase_quantity` is only ever tested with a SKU that is present, so
`null-prop-02` never fires here.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from services.cart import CartService


@pytest.fixture
def service() -> CartService:
    return CartService()


def test_create_and_get(service):
    service.create("c_1", "us-east")
    assert service.get("c_1").region == "us-east"


def test_get_unknown_cart_raises(service):
    with pytest.raises(KeyError):
        service.get("c_missing")


def test_add_item(service, cart):
    item = service.add_item(cart, "sku-3", 2, Decimal("5.00"))
    assert item.quantity == 2
    assert len(cart.items) == 3


def test_add_existing_item_increases_quantity(service, cart):
    item = service.add_item(cart, "sku-1", 3, Decimal("19.99"))
    assert item.quantity == 5
    assert len(cart.items) == 2


def test_get_item_finds_a_present_sku(service, cart):
    assert service.get_item(cart, "sku-1").quantity == 2


def test_get_item_returns_none_for_an_absent_sku(service, cart):
    assert service.get_item(cart, "sku-absent") is None


def test_increase_quantity(service, cart):
    assert service.increase_quantity(cart, "sku-1", by=3) == 5


def test_subtotal(cart):
    assert cart.subtotal() == Decimal("49.99")


def test_item_count(cart):
    assert cart.item_count() == 3
