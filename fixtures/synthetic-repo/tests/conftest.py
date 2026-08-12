"""Shared test doubles.

The suite never touches the network. Every client is stubbed, which is also
why most of the 25 bugs survived: the stubs return the happy-path shape, and
the failure paths are the ones nobody wrote a test for.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from models.cart import Cart, CartItem
from models.user import DiscountProfile, User


class StubTaxClient:
    """Returns a rate. Never the None that production returns on a 5xx."""

    def __init__(self, rate: Decimal | None = Decimal("0.20")):
        self.rate = rate
        self.calls: list[str] = []

    def get_rate(self, region: str) -> Decimal | None:
        self.calls.append(region)
        return self.rate


class StubPaymentClient:
    def __init__(self, amount: str = "49.99"):
        self.amount = amount

    def charge(self, cart_id: str, amount: str, currency: str = "USD") -> dict:
        return {"id": "ch_1", "amount": self.amount, "currency": currency}


class StubInventoryClient:
    def __init__(self) -> None:
        self.reserved: list[tuple[str, int]] = []

    def fetch_item(self, sku: str) -> dict:
        return {"sku": sku, "description": f"item {sku}", "units": 10}

    def reserve(self, sku: str, quantity: int) -> str:
        self.reserved.append((sku, quantity))
        return f"res_{sku}"

    def release(self, reservation_id: str) -> None:
        return None


@pytest.fixture
def cart() -> Cart:
    return Cart(
        id="c_8821",
        region="eu-west",
        items=[
            CartItem(sku="sku-1", quantity=2, unit_price=Decimal("19.99")),
            CartItem(sku="sku-2", quantity=1, unit_price=Decimal("10.01")),
        ],
    )


@pytest.fixture
def user() -> User:
    return User(
        id="u_9f2b1c",
        email="ada@example.com",
        plan="pro",
        discount_profile=DiscountProfile(tier="gold", percent_off=10),
    )


@pytest.fixture
def guest() -> User:
    """A user with no billing history — `discount_profile` is None."""
    return User(id="u_guest", email="guest@example.com", plan="free")
