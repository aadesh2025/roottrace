"""Webhooks — 5 tests, **1 deliberately failing**.

`test_event_summary_reports_livemode` fails and has failed since it was
written. The livemode flag was specified, the test was written first, and the
implementation never landed — the ticket was closed as "ship without it".

It is unrelated to any of the 25 fixture bugs, on purpose: gate G6 must
classify it as `already_failing`, and if it were tied to a case then fixing
that case would flip a baseline failure to passing and corrupt G6's
accounting.
"""

from __future__ import annotations

import hashlib
import hmac

from api.routes import webhooks


def _signature(body: bytes) -> str:
    return hmac.new(webhooks.WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_verify_signature_accepts_a_valid_signature():
    body = b"{}"
    assert webhooks.verify_signature({"signature": _signature(body)}, body)


def test_verify_signature_rejects_a_forged_signature():
    assert not webhooks.verify_signature({"signature": "deadbeef"}, b"{}")


def test_handled_event_types_are_registered():
    assert "payment_intent.succeeded" in webhooks.HANDLED_EVENTS


def test_event_summary_includes_the_type():
    assert webhooks.event_summary({"type": "charge.refunded", "id": "evt_1"}).startswith(
        "charge.refunded"
    )


def test_event_summary_reports_livemode():
    """FAILS ON PURPOSE — see the module docstring."""
    summary = webhooks.event_summary({"type": "charge.refunded", "id": "evt_1", "livemode": True})
    assert "livemode=True" in summary
