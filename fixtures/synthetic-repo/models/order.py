"""Order model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal


@dataclass
class Order:
    id: str
    cart_id: str
    user_id: str
    total: Decimal
    currency: str = "USD"
    status: str = "pending"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def mark_paid(self, payment_reference: str) -> None:
        self.status = "paid"
        self.payment_reference = payment_reference
