"""Dependency wiring.

Module-level singletons, built once at import. Typical of a service this age,
and the reason `services/pricing.py`'s cache is shared across requests rather
than scoped to one.
"""

from __future__ import annotations

from clients.inventory_client import InventoryClient
from clients.payment_client import PaymentClient
from clients.tax_client import TaxClient
from config.settings import load_settings
from models.user import User
from services.cart import CartService
from services.checkout import CheckoutService
from services.inventory import InventoryService

_settings = load_settings()

_cart_service = CartService()
_tax_client = TaxClient(_settings.tax_service_url or "http://tax.internal")
_payment_client = PaymentClient(_settings.payment_service_url)
_inventory_client = InventoryClient(_settings.inventory_service_url)

_checkout_service = CheckoutService(_tax_client, _payment_client)
_inventory_service = InventoryService(_inventory_client)


def get_cart_service() -> CartService:
    return _cart_service


def get_checkout_service() -> CheckoutService:
    return _checkout_service


def get_inventory_service() -> InventoryService:
    return _inventory_service


def get_current_user() -> User:
    """The authenticated caller.

    Stubbed. The real implementation reads the session; the fixture repo has
    no session store and the identity is not what any case is about.
    """
    return User(id="u_9f2b1c", email="ada@example.com", plan="pro")
