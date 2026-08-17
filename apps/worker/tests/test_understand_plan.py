"""The retrieval plan (`03` §S4) — the real output of S4.

`03` §S4's worked example is the reference throughout, because `18` §7 pins
its values as canonical and the whole corpus is built around it.
"""

from __future__ import annotations

from typing import Any

import pytest

from roottrace_worker.pipeline.understand.contracts import ExceptionFamily, Frame
from roottrace_worker.pipeline.understand.frames import PathMapping, extract_frames
from roottrace_worker.pipeline.understand.plan import (
    build_plan,
    entry_point,
    failure_point,
    implicated_symbols,
    select_breadcrumb,
)

pytestmark = pytest.mark.unit

APP_MAPPING = (PathMapping("/app/", ""),)

RAW_FRAMES: list[dict[str, Any]] = [
    {
        "file": "/app/services/checkout.py",
        "line": 142,
        "function": "calculate_total",
        "in_app": True,
        "context_line": "        subtotal = base_price + tax_amount",
        "vars": {
            "cart": "<Cart id=c_8821 region='eu-west'>",
            "base_price": "Decimal('49.99')",
            "tax_amount": "None",
        },
    },
    {
        "file": "/app/api/routes/checkout.py",
        "line": 58,
        "function": "_build_response",
        "in_app": True,
        "context_line": "    total = checkout_service.calculate_total(cart, user)",
        "vars": {"cart": "<Cart id=c_8821 region='eu-west'>"},
    },
]

BREADCRUMBS: list[dict[str, Any]] = [
    {
        "ts": "2026-08-04T09:14:22.101Z",
        "category": "db",
        "message": "SELECT * FROM carts WHERE id=? (12ms)",
        "level": "info",
    },
    {
        "ts": "2026-08-04T09:14:22.340Z",
        "category": "http",
        "message": "GET tax-service/rate?region=eu-west -> 503",
        "level": "warning",
    },
]

ERROR_AT = "2026-08-04T09:14:22.481Z"


@pytest.fixture
def frames() -> tuple[Frame, ...]:
    return extract_frames({"stack_frames": RAW_FRAMES}, mappings=APP_MAPPING)


# ── Entry and failure points ───────────────────────────────────────────────


def test_the_failure_point_is_the_innermost_in_app_frame(frames: tuple[Frame, ...]) -> None:
    point = failure_point(frames)
    assert point is not None
    assert (point.repo_path, point.function, point.line) == (
        "services/checkout.py",
        "calculate_total",
        142,
    )


def test_the_entry_point_is_the_outermost_in_app_frame(frames: tuple[Frame, ...]) -> None:
    point = entry_point(frames, {"method": "POST", "route_pattern": "/api/v2/checkout"})
    assert point is not None
    assert point.handler == "api/routes/checkout.py::_build_response"
    assert (point.method, point.pattern) == ("POST", "/api/v2/checkout")


def test_with_no_in_app_frames_the_route_still_gives_an_entry_point() -> None:
    """`03` §S4: *continue with entry point from `request.route_pattern`*.
    The handler is unknown, but the route tells S5 which endpoint to look
    for."""
    point = entry_point((), {"method": "POST", "route_pattern": "/api/v2/checkout"})
    assert point is not None
    assert point.handler is None
    assert point.pattern == "/api/v2/checkout"


def test_a_background_job_has_no_entry_point() -> None:
    assert entry_point((), None) is None


# ── Implicated symbols ─────────────────────────────────────────────────────


def test_the_symbols_from_the_spec_example(frames: tuple[Frame, ...]) -> None:
    """`03` §S4 lists exactly these, in this order: the failing function, the
    two locals on the failing line, then the caller."""
    assert implicated_symbols(frames, RAW_FRAMES, "") == (
        "calculate_total",
        "base_price",
        "tax_amount",
        "_build_response",
    )


def test_locals_not_on_the_failing_line_are_excluded(frames: tuple[Frame, ...]) -> None:
    """`cart` is in scope and irrelevant. Intersecting the source line with the
    captured locals is what separates two names from a dozen."""
    assert "cart" not in implicated_symbols(frames, RAW_FRAMES, "")


def test_symbols_from_the_message_are_included(frames: tuple[Frame, ...]) -> None:
    symbols = implicated_symbols(
        frames, RAW_FRAMES, "apply_discount() missing 1 required positional argument: 'region'"
    )
    assert "apply_discount" in symbols
    assert "region" in symbols


# ── Breadcrumb selection ───────────────────────────────────────────────────


def test_the_canonical_breadcrumb_offset() -> None:
    """`18` §7 pins the reference case's breadcrumb at **T-141 ms**. If this
    number moves, either the fixture or the arithmetic has drifted."""
    signal = select_breadcrumb(BREADCRUMBS, ERROR_AT)
    assert signal is not None
    assert signal.offset_ms == 141
    assert signal.index == 1
    assert "141 ms before the error" in signal.text


def test_a_warning_outranks_a_closer_info() -> None:
    """Level first, proximity second. The `db` breadcrumb is nearer in the
    list but says nothing was wrong."""
    signal = select_breadcrumb(BREADCRUMBS, ERROR_AT)
    assert signal is not None
    assert "tax-service" in signal.text


def test_the_nearest_wins_within_one_level() -> None:
    crumbs = [
        {"ts": "2026-08-04T09:14:20.000Z", "message": "far", "level": "warning"},
        {"ts": "2026-08-04T09:14:22.400Z", "message": "near", "level": "warning"},
    ]
    signal = select_breadcrumb(crumbs, ERROR_AT)
    assert signal is not None
    assert "near" in signal.text


def test_breadcrumbs_after_the_error_are_not_the_trigger() -> None:
    crumbs = [{"ts": "2026-08-04T09:14:23.000Z", "message": "after", "level": "error"}]
    assert select_breadcrumb(crumbs, ERROR_AT) is None


def test_no_breadcrumbs_is_no_signal() -> None:
    assert select_breadcrumb([], ERROR_AT) is None
    assert select_breadcrumb(None, ERROR_AT) is None


def test_selection_survives_an_unparseable_timestamp() -> None:
    """A breadcrumb with a broken `ts` must not take the stage down; it just
    cannot be ranked by proximity."""
    crumbs = [{"ts": "not-a-time", "message": "still useful", "level": "error"}]
    signal = select_breadcrumb(crumbs, ERROR_AT)
    assert signal is not None
    assert signal.offset_ms is None


# ── The plan ───────────────────────────────────────────────────────────────


def test_the_plan_from_the_spec_example(frames: tuple[Frame, ...]) -> None:
    plan = build_plan(
        frames=frames,
        raw_frames=RAW_FRAMES,
        family=ExceptionFamily.NULL_UNDEFINED,
        message="unsupported operand type(s) for +: 'decimal.Decimal' and 'NoneType'",
        breadcrumbs=BREADCRUMBS,
        error_timestamp=ERROR_AT,
        symbols=implicated_symbols(frames, RAW_FRAMES, ""),
    )
    assert plan.must_fetch == ("services/checkout.py", "api/routes/checkout.py")
    assert plan.want_git_history_for == ("services/checkout.py",)
    assert plan.want_tests_for == ("calculate_total",)
    assert plan.breadcrumb_signal is not None


def test_the_null_variable_drives_a_producer_query(frames: tuple[Frame, ...]) -> None:
    """`A2` §3's rule in capitals: when a value is unexpectedly None, the
    defect is in whatever PRODUCED it. `tax_amount` is the local holding the
    null, and the query asks for its producer — the file that turns out to
    hold the root cause and appears in no frame."""
    plan = build_plan(
        frames=frames,
        raw_frames=RAW_FRAMES,
        family=ExceptionFamily.NULL_UNDEFINED,
        message="",
        breadcrumbs=None,
        symbols=(),
    )
    assert "where tax_amount is produced or returned" in plan.semantic_queries
    assert not any("base_price" in query for query in plan.semantic_queries)


def test_a_file_appearing_twice_is_fetched_once() -> None:
    """`null-prop-04` and `race-02` both fail and are called within one file."""
    raw = [
        {"file": "/app/services/checkout.py", "line": 86, "function": "_discount_percent"},
        {"file": "/app/services/checkout.py", "line": 96, "function": "discounted_subtotal"},
    ]
    frames = extract_frames({"stack_frames": raw}, mappings=APP_MAPPING)
    plan = build_plan(
        frames=frames,
        raw_frames=raw,
        family=ExceptionFamily.NULL_UNDEFINED,
        message="",
        breadcrumbs=None,
    )
    assert plan.must_fetch == ("services/checkout.py",)


def test_an_unresolved_frame_contributes_no_path() -> None:
    """S5 cannot fetch what S4 could not name, and a plan carrying `None`
    would be a fetch loop's crash."""
    raw = [{"file": "/opt/mystery/thing.py", "line": 4, "function": "f"}]
    frames = extract_frames({"stack_frames": raw})
    plan = build_plan(
        frames=frames,
        raw_frames=raw,
        family=ExceptionFamily.UNCLASSIFIED,
        message="",
        breadcrumbs=None,
    )
    assert plan.must_fetch == ()
