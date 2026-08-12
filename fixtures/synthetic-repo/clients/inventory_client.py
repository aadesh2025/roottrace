"""HTTP client for the inventory service.

`external-03`: `reserve` has no circuit breaker. When the service's DNS record
goes stale every request spends the full timeout before failing, and the
checkout path serialises on it — one dependency failing takes the whole service
down rather than degrading it.

`type-mismatch-02`: `fetch_item` returns the decoded JSON dict directly. Callers
were written against `InventoryItem` and access attributes on it.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from clients.errors import UpstreamTimeout, UpstreamUnavailable

logger = logging.getLogger(__name__)


class InventoryClient:
    def __init__(self, base_url: str, timeout: float = 2.0):
        self._base_url = base_url
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._timeout = timeout

    def close(self) -> None:
        self._client.close()

    def fetch_item(self, sku: str) -> dict[str, Any]:
        # Returns the raw payload. `services/inventory.py` treats the result as
        # an InventoryItem, which it has never been.
        resp = self._client.get(f"/items/{sku}")
        resp.raise_for_status()
        return dict(resp.json())

    def reserve(self, sku: str, quantity: int) -> str:
        # Failures are typed and named, which is right. What is missing is a
        # circuit breaker: every caller waits the full timeout, every time, for
        # as long as the outage lasts, instead of failing fast after the first.
        try:
            resp = self._client.post("/reserve", json={"sku": sku, "quantity": quantity})
            if resp.status_code >= 500:
                raise UpstreamUnavailable("inventory", resp.status_code)
            resp.raise_for_status()
            return str(resp.json()["reservation_id"])
        except httpx.ConnectError as exc:
            raise UpstreamUnavailable("inventory") from exc
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout("inventory", self._timeout) from exc

    def ping(self) -> bool:
        """Liveness check for the inventory service.

        Written defensively, as health checks usually are: every transport
        failure becomes a typed, named error rather than leaking an httpx
        exception at the caller. There is no defect here, which is the point —
        `unfixable-02` reaches the platform's DNS failure through this method,
        and the honest answer is that no change to this repository fixes it.
        """
        try:
            return self._client.get("/health").status_code == 200
        except httpx.ConnectError as exc:
            raise UpstreamUnavailable("inventory") from exc
        except httpx.TimeoutException as exc:
            raise UpstreamTimeout("inventory", self._timeout) from exc

    def release(self, reservation_id: str) -> None:
        self._client.post("/release", json={"reservation_id": reservation_id})
