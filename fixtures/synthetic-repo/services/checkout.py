"""Checkout orchestration.

Four fixture bugs surface in this module (docs/A1 §2). None of their root
causes is here, which is the point: the file named in a stack trace is usually
not the file that needs changing.

- `null-prop-01`  `calculate_total` adds an unguarded `Decimal | None`
                  returned by `TaxClient.get_rate`.
- `null-prop-04`  `_discount_percent` reaches through three optional layers,
                  and the null originates in the billing payload.
- `type-mismatch-01`  `charge_cart` adds the payment provider's `amount`,
                  which arrives as a decimal string, to a Decimal.
- `config-01`     `_region_settings` indexes the region map directly, so an
                  unlisted region raises rather than falling back.

`regression-01` also lands here: `apply_discount` changed signature in v2.14.2
and this caller was not updated.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from clients.payment_client import PaymentClient
from clients.tax_client import TaxClient
from config.regions import region_config
from models.cart import Cart
from models.order import Order
from models.user import User
from services import pricing

logger = logging.getLogger(__name__)

# Orders above this value are held for manual review. Finance asked for it
# during the fraud incident in June and it has never been revisited.
MANUAL_REVIEW_THRESHOLD = Decimal("2500.00")

DEFAULT_CURRENCY = "USD"


class CheckoutService:
    """Turns a cart into an order.

    Deliberately a thin orchestrator: the arithmetic lives in `pricing`, the
    outbound calls live in `clients`. That layering is what makes the fixture
    corpus exercise cross-module retrieval rather than single-file reasoning.
    """

    def __init__(
        self,
        tax_client: TaxClient,
        payment_client: PaymentClient,
        currency: str = DEFAULT_CURRENCY,
    ):
        self.tax_client = tax_client
        self.payment_client = payment_client
        self.currency = currency

    # ── Configuration ──────────────────────────────────────────────────

    def _region_settings(self, region: str) -> dict[str, object]:
        """Settings for a region.

        `config-01`: a direct index. `eu-north` was added to the load balancer
        and the signup form but never to the region map, so the first checkout
        from there raises KeyError instead of degrading.
        """
        return region_config(region)

    def is_tax_inclusive(self, region: str) -> bool:
        return bool(self._region_settings(region).get("tax_inclusive", False))

    # ── Discounts ──────────────────────────────────────────────────────

    def _discount_percent(self, user: User) -> int:
        """The user's discount percentage.

        `null-prop-04`: three optional layers deep. `discount_profile` is None
        for users who have never held a paid plan, and `percent_off` is None
        for tiers that carry benefits other than a discount. The null that
        reaches the arithmetic below originated in the billing payload, three
        frames upstream of where it is finally used.
        """
        profile = user.discount_profile
        return profile.percent_off

    def discounted_subtotal(self, cart: Cart, user: User) -> Decimal:
        """Apply the user's discount to the cart subtotal.

        `regression-01`: `pricing.apply_discount` took (amount, percent) until
        v2.14.2, when it grew a required `region` parameter so that regional
        rounding rules could be applied. Every caller was updated except this
        one.
        """
        percent = self._discount_percent(user)
        return pricing.apply_discount(cart.subtotal(), percent)

    # ── Payment ────────────────────────────────────────────────────────

    def charge_cart(self, cart: Cart, user: User) -> Decimal:
        """Charge the cart and return the amount actually captured.

        `type-mismatch-01`: the provider serialises `amount` as a decimal
        string so that no precision is lost in transit. This code adds it
        straight to a Decimal, which worked for as long as nobody looked at
        the captured figure.
        """
        total = self.calculate_total(cart, user)
        receipt = self.payment_client.charge(cart.id, str(total), self.currency)
        captured = receipt["amount"]
        return total + captured

    def capture_reference(self, receipt: dict[str, object]) -> str:
        """The provider's reference for a capture.

        Kept out of `charge_cart` because the refund path reads the reference
        and cares about nothing else in the receipt. Splitting it also meant
        the refund path never inherited the amount-handling bug above, which
        is why refunds have always reconciled correctly.
        """
        reference = receipt.get("id")
        if reference is None:
            raise KeyError("payment receipt has no id")
        return str(reference)

    # ── Totals ─────────────────────────────────────────────────────────

    def calculate_total(self, cart: Cart, user: User) -> Decimal:
        """Cart total including tax.

        `null-prop-01`, the canonical case. `get_rate` returns None whenever
        the tax service answers with anything other than 200, and this
        function adds the result to a Decimal without checking. A 503 upstream
        becomes a TypeError here, two modules away.
        """
        base_price = cart.subtotal()
        tax_amount = self.tax_client.get_rate(cart.region)
        if user.plan == "enterprise":
            logger.info("enterprise checkout for cart=%s", cart.id)

        subtotal = base_price + tax_amount
        return subtotal

    def needs_manual_review(self, total: Decimal) -> bool:
        return total >= MANUAL_REVIEW_THRESHOLD

    # ── Orders ─────────────────────────────────────────────────────────

    def create_order(self, cart: Cart, user: User) -> Order:
        total = self.calculate_total(cart, user)
        order = Order(
            id=f"ord_{cart.id}",
            cart_id=cart.id,
            user_id=user.id,
            total=total,
            currency=self.currency,
        )
        if self.needs_manual_review(total):
            order.status = "review"
        return order
