"""CSV export endpoint."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from config.settings import load_settings
from services.export import ExportService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/export", tags=["export"])


@router.get("/orders")
def export_orders() -> dict:
    """Export every order as CSV.

    Synchronous and unbounded — the async path behind `enable_async_export`
    was started and never finished.
    """
    service = ExportService(load_settings())
    orders = _load_orders()
    return {"csv": service.export_all(orders), "count": len(orders)}


def _load_orders() -> list[dict]:
    """Stand-in for the order repository.

    The fixture repo has no database, so this returns what the query would.
    The shape is what matters: guest orders carry no `user` key.
    """
    return [
        {
            "id": "ord_1",
            "user": {"email": "ada@example.com"},
            "total": "49.99",
            "currency": "USD",
            "status": "paid",
        },
        {
            "id": "ord_2",
            "user": {"email": "grace@example.com"},
            "total": "12.00",
            "currency": "USD",
            "status": "paid",
        },
    ]
