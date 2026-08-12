"""HTTP client for the payment provider.

`external-02`: `charge` treats 429 as a hard failure. The provider returns it
routinely during flash sales and includes a `Retry-After` header, which this
client reads and then ignores.

`type-mismatch-01`: `charge` returns the provider's `amount` verbatim, and the
provider sends it as a decimal STRING. `services/checkout.py` adds it to a
Decimal.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from clients.errors import RateLimited, UpstreamUnavailable

logger = logging.getLogger(__name__)


class PaymentClient:
    def __init__(self, base_url: str, timeout: float = 5.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def charge(self, cart_id: str, amount: str, currency: str = "USD") -> dict[str, Any]:
        resp = self._client.post(
            "/charges", json={"cart_id": cart_id, "amount": amount, "currency": currency}
        )
        if resp.status_code == 429:
            # The header is read and then thrown away. There is no retry, no
            # backoff, and no queue — the customer simply sees a failure.
            retry_after = resp.headers.get("Retry-After")
            logger.warning("payment provider rate limited us, retry_after=%s", retry_after)
            raise RateLimited("payments", float(retry_after) if retry_after else None)
        if resp.status_code >= 500:
            raise UpstreamUnavailable("payments", resp.status_code)
        resp.raise_for_status()
        return dict(resp.json())

    def refund(self, charge_id: str) -> dict[str, Any]:
        resp = self._client.post(f"/charges/{charge_id}/refund")
        resp.raise_for_status()
        return dict(resp.json())
