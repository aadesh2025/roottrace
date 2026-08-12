"""Quote service — 3 tests, all passing.

`test_estimate_with_missing_tax` is the one that matters. It asserts the
CURRENT contract: when the tax service is unreachable, the estimate falls back
to an untaxed total rather than failing.

That contract is what makes `regression-02` a repair-loop case. The obvious
first patch for `null-prop-01` — make `get_rate` raise — breaks this test,
gate G6 catches it, and attempt 2 has to find a fix that satisfies both callers.
The test is correct as written; the patch has to be better.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from services.quote import QuoteService
from tests.conftest import StubTaxClient


@pytest.fixture
def service() -> QuoteService:
    return QuoteService(StubTaxClient())


def test_estimate_total_includes_tax(service, cart):
    assert service.estimate_total(cart) == Decimal("59.988")


def test_estimate_with_missing_tax(cart):
    """No tax service configured → the estimate is the untaxed subtotal.

    This is deliberate behaviour for local development and for the seconds
    after a deploy when the tax service has not yet registered. A patch that
    makes `get_rate` raise breaks it.
    """
    service = QuoteService(StubTaxClient(rate=None))
    assert service.estimate_total(cart) == Decimal("49.99")


def test_estimate_with_shipping(service, cart):
    assert service.estimate_with_shipping(cart, Decimal("5.00")) == Decimal("64.988")
