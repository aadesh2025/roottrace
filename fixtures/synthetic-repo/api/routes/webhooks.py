"""Stripe webhook receiver.

`key-error-01`: `verify_signature` indexes `headers["signature"]` directly.
Stripe sends `Stripe-Signature`, and their retry probe sends no signature
header at all — so the probe, which exists to check that the endpoint is
reachable, is the request that crashes it.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/webhooks", tags=["webhooks"])

WEBHOOK_SECRET = "whsec_fixture_not_a_real_secret"

HANDLED_EVENTS = ("payment_intent.succeeded", "payment_intent.payment_failed", "charge.refunded")


def verify_signature(headers: dict[str, str], body: bytes) -> bool:
    """Check the webhook signature.

    `key-error-01`: a direct index into a dict built from request headers,
    which are entirely under the caller's control.
    """
    provided = headers["signature"]
    expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided, expected)


@router.post("/stripe")
def receive_stripe(payload: dict, headers: dict[str, str]) -> dict:
    body = str(payload).encode()
    if not verify_signature(headers, body):
        return {"received": False, "reason": "bad signature"}

    event_type = payload.get("type", "")
    if event_type not in HANDLED_EVENTS:
        logger.info("ignoring unhandled webhook type=%s", event_type)
        return {"received": True, "handled": False}

    return {"received": True, "handled": True, "type": event_type}


def event_summary(payload: dict) -> str:
    """One-line description of a webhook event, for the audit log."""
    return f"{payload.get('type', 'unknown')} id={payload.get('id', '-')}"
