"""Application settings, read from the environment.

Two defects live here.

`null-prop-03`: `tax_service_url` is optional and defaults to None, and callers
assume a string.

`config-02`: `EXPORT_BATCH_SIZE` is set in the production manifest and missing
from staging. `export_batch_size` returns None there rather than a default, so
the failure only appears in one environment — which is why it took three weeks
to notice.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    environment: str
    tax_service_url: str | None
    inventory_service_url: str
    payment_service_url: str
    export_batch_size: int | None
    request_timeout_seconds: float = 2.0


def load_settings() -> Settings:
    batch = os.environ.get("EXPORT_BATCH_SIZE")
    return Settings(
        environment=os.environ.get("APP_ENV", "development"),
        # Optional because the tax service was introduced after this config
        # was written, and local development runs without it.
        tax_service_url=os.environ.get("TAX_SERVICE_URL"),
        inventory_service_url=os.environ.get(
            "INVENTORY_SERVICE_URL", "http://inventory.internal"
        ),
        payment_service_url=os.environ.get("PAYMENT_SERVICE_URL", "http://payments.internal"),
        # No default. Present in the production manifest, absent in staging.
        export_batch_size=int(batch) if batch else None,
    )
