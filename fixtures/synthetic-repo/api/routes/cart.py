"""Cart CRUD.

`key-error-02`: `apply_coupon` reads an optional body field directly. The
field is only sent when the customer typed a code, which is the minority of
requests.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from fastapi import APIRouter, HTTPException

from api.deps import get_cart_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/cart", tags=["cart"])


@router.post("")
def create_cart(payload: dict) -> dict:
    service = get_cart_service()
    cart = service.create(payload["cart_id"], payload.get("region", "us-east"))
    return {"cart_id": cart.id, "region": cart.region}


@router.post("/{cart_id}/items")
def add_item(cart_id: str, payload: dict) -> dict:
    service = get_cart_service()
    try:
        cart = service.get(cart_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="cart not found") from None

    item = service.add_item(
        cart,
        payload["sku"],
        int(payload.get("quantity", 1)),
        Decimal(str(payload.get("unit_price", "0"))),
    )
    return {"sku": item.sku, "quantity": item.quantity}


@router.post("/{cart_id}/coupon")
def apply_coupon(cart_id: str, payload: dict) -> dict:
    """Attach a coupon code to the cart.

    `key-error-02`: `coupon_code` is optional in the client and mandatory
    here.
    """
    service = get_cart_service()
    cart = service.get(cart_id)
    cart.coupon_code = payload["coupon_code"]
    return {"cart_id": cart.id, "coupon_code": cart.coupon_code}


@router.get("/{cart_id}/items/first")
def first_item_on_page(cart_id: str, offset: int = 1) -> dict:
    """The first line item on a page — powers the "jump to item" control.

    `boundary-01` surfaces here. Pagination is 1-based, `page` slices as
    though it were 0-based, and the last page therefore comes back empty, so
    this indexes an empty list.
    """
    service = get_cart_service()
    cart = service.get(cart_id)
    items = service.page(cart, offset, 1)
    return {"sku": items[0].sku, "quantity": items[0].quantity}


@router.get("/{cart_id}/items")
def list_items(cart_id: str, offset: int = 1, limit: int = 20) -> dict:
    """Paginated line items. Pagination is 1-based in this API."""
    service = get_cart_service()
    cart = service.get(cart_id)
    items = service.page(cart, offset, limit)
    return {
        "items": [{"sku": i.sku, "quantity": i.quantity} for i in items],
        "offset": offset,
        "limit": limit,
    }
