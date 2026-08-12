"""POST /api/v2/checkout.

The entry point for the canonical fixture case. The traceback that reaches the
error tracker starts here, which is exactly why the case is interesting: this
file is the first in-app frame and the last place worth changing.

It is in `must_not_modify_files` for `null-prop-01`. Catching the TypeError
here would make the symptom disappear and leave every other caller of
`TaxClient.get_rate` broken.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, HTTPException

from api.deps import get_cart_service, get_checkout_service, get_current_user
from models.cart import Cart
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2", tags=["checkout"])


@router.post("/checkout")
def create_checkout(payload: dict) -> dict:
    """Turn the caller's cart into an order.

    The handler is deliberately thin. Everything that can fail lives one layer
    down, and the only thing this function decides is which HTTP status a
    failure maps to.
    """
    cart_id = payload.get("cart_id")
    if not cart_id:
        raise HTTPException(status_code=400, detail="cart_id is required")

    cart_service = get_cart_service()
    checkout_service = get_checkout_service()
    user = get_current_user()

    try:
        cart = cart_service.get(cart_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="cart not found") from None

    return _build_response(cart, user, checkout_service)


def _build_response(cart: Cart, user: User, checkout_service) -> dict:
    """Assemble the checkout response.

    Split out of the handler during the v2.14.1 tidy-up so the handler could
    stay readable.
    """
    total = checkout_service.calculate_total(cart, user)
    order = checkout_service.create_order(cart, user)
    return {
        "order_id": order.id,
        "total": str(total),
        "currency": order.currency,
        "status": order.status,
    }


@router.get("/checkout/{order_id}")
def get_checkout(order_id: str) -> dict:
    return {"order_id": order_id, "status": "pending"}


def _as_decimal(value: str) -> Decimal:
    return Decimal(value)
