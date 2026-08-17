"""The SDK and the API must agree (T2.5).

The SDK declares no dependencies — it is installed into customer applications,
so every dependency it names is a version constraint imposed on someone else's
resolver. That is a deliberate choice with a deliberate cost: three things are
duplicated between `packages/sdk-python` and `apps/api`, and duplication that
nothing checks is duplication that drifts.

This file is the check. It is the only place both packages are imported
together, and it fails if:

- the SDK's event payload stops satisfying the server's own validator;
- the two `HEADER_ALLOWLIST`s diverge;
- the two UUIDv7 implementations produce different layouts.

A unit test in either package would pass while the other half changed.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from roottrace_api.ids import uuid7 as api_uuid7
from roottrace_api.ingest.events import validate_batch
from roottrace_api.ingest.fingerprint import compute_fingerprint, fingerprint_input
from roottrace_api.ingest.sanitise import HEADER_ALLOWLIST as API_ALLOWLIST
from roottrace_api.ingest.sanitise import sanitise
from roottrace_sdk._config import HEADER_ALLOWLIST as SDK_ALLOWLIST
from roottrace_sdk._config import Config
from roottrace_sdk._event import build_event
from roottrace_sdk._ids import uuid7 as sdk_uuid7

pytestmark = pytest.mark.integration

TEST_API_KEY = "rt_test_" + "0" * 32


def sdk_event(**kwargs: Any) -> dict[str, Any]:
    config = Config(
        api_key=TEST_API_KEY,
        environment="production",
        service="checkout-api",
        release="v2.14.3",
    )

    def calculate_total(base_price: float, tax_amount: Any) -> float:
        return base_price + tax_amount

    try:
        calculate_total(49.99, None)
    except TypeError as exc:
        return build_event(exc, config=config, **kwargs)
    raise AssertionError("expected a TypeError")


# ── The payload the server actually accepts ────────────────────────────────


def test_a_plain_sdk_event_is_accepted_by_the_servers_validator() -> None:
    """`03` §S1's schema, enforced by the code that enforces it in production.

    A shape asserted only inside the SDK's own tests proves that the SDK is
    self-consistent, which is not the property that matters.
    """
    result = validate_batch([sdk_event()])

    assert result.rejected == (), [item.as_error() for item in result.rejected]
    assert len(result.accepted) == 1


def test_every_optional_block_the_sdk_can_emit_is_accepted() -> None:
    """The blocks are optional to the server and independent of each other, so
    a field name the SDK got wrong in one of them would not show up in the
    minimal case above."""
    event = sdk_event(
        breadcrumbs=[
            {
                "ts": "2026-08-04T09:14:22.101Z",
                "category": "db",
                "message": "SELECT",
                "level": "info",
            }
        ],
        tags={"region": "eu-west-1"},
        extra={"cart_item_count": 3},
        user_context={"user_hash": "u_9f2b1c", "plan": "pro", "is_authenticated": True},
        request={
            "method": "POST",
            "url": "/api/v2/checkout",
            "route_pattern": "/api/v2/checkout",
            "status_code": 500,
            "duration_ms": 412,
            "headers": {"content-type": "application/json"},
            "query_params": {"coupon": "SAVE20"},
        },
        framework="fastapi",
        framework_version="0.111.0",
    )

    result = validate_batch([event])

    assert result.rejected == (), [item.as_error() for item in result.rejected]


def test_every_level_the_sdk_emits_is_a_level_the_server_knows() -> None:
    for level in ("error", "fatal", "warning"):
        result = validate_batch([sdk_event(level=level)])
        assert result.rejected == (), f"{level} was rejected"


def test_the_sdks_frames_are_usable_by_the_fingerprint() -> None:
    """`02` §S2 builds the fingerprint from `basename::function` of the deepest
    in-app frames. If the SDK marked every frame in-app, or omitted `in_app`
    altogether, this would silently fingerprint on the wrong thing — a wrong
    grouping, not an error."""
    event = sdk_event()
    error_type, _message, frames, _route = fingerprint_input(event)

    assert error_type == "TypeError"
    assert "calculate_total" in frames
    assert "site-packages" not in frames
    assert len(compute_fingerprint(event)) == 32


def test_the_same_error_from_the_sdk_fingerprints_stably() -> None:
    """Two occurrences of one bug must be one issue. The frame list carries
    line numbers and memory addresses in the wrong hands; the fingerprint must
    be blind to both."""
    assert compute_fingerprint(sdk_event()) == compute_fingerprint(sdk_event())


def test_the_sdks_payload_survives_the_servers_sanitiser() -> None:
    """The pass runs over every accepted event before anything is stored. A
    payload it mangles or rejects would fail in production and nowhere else."""
    cleaned, _redactions = sanitise(sdk_event())

    assert cleaned["error"]["type"] == "TypeError"
    assert cleaned["error"]["stack_frames"]


# ── The duplicated constants ───────────────────────────────────────────────


def test_the_header_allowlists_are_identical() -> None:
    """The SDK applies it so an `Authorization` header never leaves the
    customer's process; the server applies it for clients that are not this
    SDK. Two lists that drift give one of the two a hole."""
    assert SDK_ALLOWLIST == API_ALLOWLIST


def test_the_two_uuid7_implementations_agree() -> None:
    """`_ids.py` is a deliberate copy of `roottrace_api/ids.py` because the SDK
    can declare no dependencies. This is what stops the copy becoming a
    different id scheme than the one the database and the API mint."""
    now_ms = 1_754_301_600_123

    api_value = api_uuid7(now_ms=now_ms)
    sdk_value = sdk_uuid7(now_ms=now_ms)

    assert api_value.version == sdk_value.version == 7
    assert api_value.variant == sdk_value.variant == uuid.RFC_4122
    # The 48-bit millisecond prefix is the ordered part and must be identical.
    assert api_value.int >> 80 == sdk_value.int >> 80 == now_ms


def test_the_sdk_declares_no_runtime_dependencies() -> None:
    """The reason the duplication above exists. If this ever stops being true
    the copies should be replaced with a shared package, not left as copies."""
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    manifest = tomllib.loads((root / "packages/sdk-python/pyproject.toml").read_text())

    assert manifest["project"]["dependencies"] == []
