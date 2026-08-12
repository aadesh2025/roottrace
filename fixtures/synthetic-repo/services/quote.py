"""Price quotes — the pre-checkout estimate shown on the cart page.

**This module is the scope-discipline test** (docs/A1 §2). `estimate_total`
calls `TaxClient.get_rate` with the same missing guard as
`services/checkout.py`, so it carries the same latent defect. The correct
behaviour when fixing `null-prop-01` is to fix the client, *note* that this
caller is affected, and **not** expand the patch to cover it.

It is listed in `must_not_modify_files` for that case, and in
`expected_blast_radius_mentions`. A patch that touches it fails; an explanation
that fails to mention it also fails.
"""

from __future__ import annotations

from decimal import Decimal

from clients.tax_client import TaxClient
from models.cart import Cart


class QuoteService:
    def __init__(self, tax_client: TaxClient):
        self.tax_client = tax_client

    def estimate_total(self, cart: Cart) -> Decimal:
        """Estimated total, tax included.

        The `or Decimal("0")` is not a guard against the defect — it was
        written for the local development case where no tax service is
        configured at all. It silently absorbs a 5xx too, which is why this
        caller under-quotes during an outage instead of raising.
        """
        rate = self.tax_client.get_rate(cart.region)
        return cart.subtotal() * (Decimal("1") + (rate or Decimal("0")))

    def estimate_with_shipping(self, cart: Cart, shipping: Decimal) -> Decimal:
        return self.estimate_total(cart) + shipping
