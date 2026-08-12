"""Inventory service — 8 tests, all passing.

Single-threaded throughout, so `race-01` cannot appear. That is exactly how it
reached production.
"""

from __future__ import annotations

import pytest

from services.inventory import InventoryService
from tests.conftest import StubInventoryClient


@pytest.fixture
def service() -> InventoryService:
    return InventoryService(StubInventoryClient())


def test_set_and_read_stock(service):
    service.set_stock("sku-1", 5)
    assert service.available("sku-1") == 5


def test_unknown_sku_has_no_stock(service):
    assert service.available("sku-absent") == 0


def test_decrement_reduces_stock(service):
    service.set_stock("sku-1", 5)
    assert service.decrement("sku-1", 2) == 3


def test_decrement_to_zero(service):
    service.set_stock("sku-1", 1)
    assert service.decrement("sku-1") == 0


def test_decrement_beyond_stock_raises(service):
    service.set_stock("sku-1", 1)
    with pytest.raises(ValueError):
        service.decrement("sku-1", 2)


def test_reserve_for_cart_reserves_every_line(service, cart):
    assert service.reserve_for_cart(cart) == ["res_sku-1", "res_sku-2"]


def test_reserve_for_cart_passes_quantities(service, cart):
    service.reserve_for_cart(cart)
    assert service.client.reserved == [("sku-1", 2), ("sku-2", 1)]


def test_release_all_is_tolerant(service):
    service.release_all(["res_1", "res_2"])
