"""Cart and line-item models."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class CartItem:
    sku: str
    quantity: int
    unit_price: Decimal

    def line_total(self) -> Decimal:
        return self.unit_price * self.quantity


@dataclass
class Cart:
    id: str
    region: str
    items: list[CartItem] = field(default_factory=list)
    coupon_code: str | None = None

    def subtotal(self) -> Decimal:
        return sum((item.line_total() for item in self.items), Decimal("0"))

    def item_count(self) -> int:
        return sum(item.quantity for item in self.items)

    def __repr__(self) -> str:
        return f"<Cart id={self.id} region={self.region!r}>"
