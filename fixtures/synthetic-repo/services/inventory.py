"""Inventory reservation.

`race-01`: `decrement` is read-modify-write with no lock. Two concurrent
checkouts for the last unit both read 1, both write 0, and both succeed —
the shop sells stock it does not have. It reproduces reliably under threads
and never once in a single-threaded test, which is why it survived review.

`external-01`: `reserve_for_cart` has no retry and no deadline of its own. It
inherits the client's 2-second timeout per call and makes one call per line
item, so a slow inventory service turns into a request that takes as long as
the cart is large. **The stack trace for this case points at the generic
timeout handler and says nothing about inventory** — only the breadcrumbs show
the 30-second call that preceded it.

`type-mismatch-02`: `describe` treats the client's dict as an object.
"""

from __future__ import annotations

import logging

from clients.errors import UpstreamTimeout
from clients.inventory_client import InventoryClient
from models.cart import Cart

logger = logging.getLogger(__name__)


class InventoryService:
    def __init__(self, client: InventoryClient):
        self.client = client
        self._stock: dict[str, int] = {}

    def set_stock(self, sku: str, units: int) -> None:
        self._stock[sku] = units

    def available(self, sku: str) -> int:
        return self._stock.get(sku, 0)

    def decrement(self, sku: str, quantity: int = 1) -> int:
        """Take units out of stock.

        `race-01`. Read, check, write — with nothing holding the three
        together. Under concurrency the check is stale by the time the write
        lands.
        """
        current = self._stock.get(sku, 0)
        if current < quantity:
            raise ValueError(f"insufficient stock for {sku}")
        remaining = current - quantity
        self._stock[sku] = remaining
        return remaining

    def describe(self, sku: str) -> str:
        """A human-readable line for the SKU.

        `type-mismatch-02`: `fetch_item` hands back the decoded JSON dict. This
        was written against an `InventoryItem` dataclass that the client
        stopped returning when it was rewritten around httpx.
        """
        item = self.client.fetch_item(sku)
        return f"{item.sku}: {item.description}"

    def reserve_for_cart(self, cart: Cart) -> list[str]:
        """Reserve every line item.

        `external-01`. One serial call per item, no retry, no overall
        deadline. When the inventory service degrades, this is where the
        request time goes — and the traceback that reaches the error tracker
        blames the timeout handler, not this loop.
        """
        reservations: list[str] = []
        for item in cart.items:
            reservations.append(self.client.reserve(item.sku, item.quantity))
        return reservations

    def release_all(self, reservation_ids: list[str]) -> None:
        for reservation_id in reservation_ids:
            try:
                self.client.release(reservation_id)
            except UpstreamTimeout:
                logger.warning("could not release reservation %s", reservation_id)
