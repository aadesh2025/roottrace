"""HTTP client for the tax-rate service.

`null-prop-01`, the canonical fixture bug (docs/A1 §4). Before commit 8a3f1c2
this lookup was inline in `services/checkout.py` and raised on a non-200. The
refactor moved it here and turned that raise into `return None` without
updating either caller. Both still assume a Decimal comes back.
"""

from __future__ import annotations

import logging
from decimal import Decimal

import httpx

logger = logging.getLogger(__name__)


class TaxClient:
    """Tax rates, by region.

    The service belongs to another team and publishes a 99.5% SLO — high
    enough that the failure path is almost never exercised in staging, and low
    enough that it fires in production most weeks.
    """

    def __init__(self, base_url: str, timeout: float = 2.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def health(self) -> bool:
        return self._client.get("/health").status_code == 200

    def get_rate(self, region: str) -> Decimal | None:
        try:
            resp = self._client.get("/rate", params={"region": region})
            resp.raise_for_status()
            return Decimal(resp.json()["rate"])
        except httpx.HTTPStatusError:
            logger.warning("tax service returned an error for region=%s", region)
            return None
