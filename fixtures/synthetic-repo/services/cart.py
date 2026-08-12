"""Cart operations.

`null-prop-02`: `get_item` returns None for an unknown SKU and every caller
assumes an item came back.

`regression-03`: `subtotal_with_tax` changed behaviour in v2.14.2 without
changing its signature — it used to include tax, and now excludes it. Nothing
in the type system noticed, and the callers that relied on the old meaning
still compile, still run, and are now wrong by exactly the tax amount.
"""

from __future__ import annotations

from decimal import Decimal

from models.cart import Cart, CartItem


class CartService:
    def __init__(self) -> None:
        self._carts: dict[str, Cart] = {}

    def create(self, cart_id: str, region: str) -> Cart:
        cart = Cart(id=cart_id, region=region)
        self._carts[cart_id] = cart
        return cart

    def get(self, cart_id: str) -> Cart:
        return self._carts[cart_id]

    def get_item(self, cart: Cart, sku: str) -> CartItem | None:
        # Returns None rather than raising. Reads fine at the definition and
        # is wrong at all four call sites.
        for item in cart.items:
            if item.sku == sku:
                return item
        return None

    def increase_quantity(self, cart: Cart, sku: str, by: int = 1) -> int:
        """Increase the quantity of a line item.

        `null-prop-02`: `get_item` returns None for a SKU that is not in the
        cart — which happens whenever a stale browser tab posts against a cart
        that has since been emptied.
        """
        item = self.get_item(cart, sku)
        item.quantity += by
        return item.quantity

    def add_item(self, cart: Cart, sku: str, quantity: int, unit_price: Decimal) -> CartItem:
        existing = self.get_item(cart, sku)
        if existing is not None:
            existing.quantity += quantity
            return existing
        item = CartItem(sku=sku, quantity=quantity, unit_price=unit_price)
        cart.items.append(item)
        return item

    def subtotal_with_tax(self, cart: Cart, tax_rate: Decimal) -> Decimal:
        """Cart subtotal.

        `regression-03`. Before v2.14.2 this returned the subtotal *including*
        tax, matching its name. The tax-inclusive display work moved that
        responsibility to the presentation layer and changed the body here,
        leaving the name and the signature untouched.
        """
        return cart.subtotal()

    def assert_display_matches_charge(
        self, cart: Cart, tax_rate: Decimal, charged: Decimal
    ) -> None:
        """The number on the cart page must equal the number charged.

        `regression-03` surfaces here. `subtotal_with_tax` stopped applying the
        rate in v2.14.2 without its name or signature changing, so the cart
        page shows the untaxed figure while checkout charges the taxed one.
        """
        displayed = self.subtotal_with_tax(cart, tax_rate)
        if displayed != charged:
            raise ValueError(
                f"cart {cart.id} displays {displayed} but checkout charges {charged}"
            )

    def page(self, cart: Cart, offset: int, limit: int) -> list[CartItem]:
        """One page of line items.

        `boundary-01`: the API's pagination is 1-based — `offset=1` is the
        first page, as the route signature says — and this slices as though it
        were 0-based. Every page is shifted by one, so the first line item is
        unreachable from any valid offset and the last page comes back short.
        """
        return cart.items[offset : offset + limit]
