"""User model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class User:
    id: str
    email: str
    plan: str = "free"
    is_authenticated: bool = True
    # Populated from the billing service. Absent for users who have never had a
    # paid plan, which is most of them.
    discount_profile: DiscountProfile | None = None


@dataclass
class DiscountProfile:
    tier: str
    percent_off: int | None = None
