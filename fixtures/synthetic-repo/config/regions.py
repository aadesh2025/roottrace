"""Region metadata.

`config-01`: `eu-north` was added to the load balancer and to the signup form,
but never here. `region_config` indexes directly, so the first checkout from
that region raises KeyError rather than falling back.
"""

from __future__ import annotations

REGION_CONFIG: dict[str, dict[str, object]] = {
    "us-east": {"currency": "USD", "tax_inclusive": False, "warehouse": "wh-1"},
    "us-west": {"currency": "USD", "tax_inclusive": False, "warehouse": "wh-2"},
    "eu-west": {"currency": "EUR", "tax_inclusive": True, "warehouse": "wh-3"},
    "ap-south": {"currency": "INR", "tax_inclusive": True, "warehouse": "wh-4"},
}

SUPPORTED_CURRENCIES = ("USD", "EUR", "INR", "GBP")


def region_config(region: str) -> dict[str, object]:
    return REGION_CONFIG[region]


def currency_for(region: str) -> str:
    return str(region_config(region)["currency"])
