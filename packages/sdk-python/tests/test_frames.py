"""Traceback parsing (T2.5, `03` §S1's `error.stack_frames`).

`in_app` is the field with consequences. It decides which frames the
fingerprint is built from (`02` §S2: `basename::function` of the deepest in-app
frames) and where retrieval looks. Marking `site-packages` frames in-app sends
retrieval into a dependency that is not in the customer's repository, and the
resulting miss looks like a retrieval failure rather than a parsing bug.
"""

from __future__ import annotations

import linecache
import sysconfig
from pathlib import Path
from typing import Any

import pytest

from roottrace_sdk._frames import format_exception, is_in_app, parse_frames, runtime_context

pytestmark = pytest.mark.unit


def _raise_through_two_frames() -> BaseException:
    def inner(value: int) -> int:
        return value + None  # type: ignore[operator]

    def outer() -> int:
        return inner(1)

    try:
        outer()
    except TypeError as exc:
        return exc
    raise AssertionError("expected a TypeError")


# ── in_app ─────────────────────────────────────────────────────────────────


def test_this_test_file_is_in_app() -> None:
    assert is_in_app(__file__)


def test_site_packages_is_not_in_app() -> None:
    assert not is_in_app(str(Path("/srv/app/.venv/lib/python3.12/site-packages/urllib3/util.py")))


def test_the_standard_library_is_not_in_app() -> None:
    stdlib = sysconfig.get_paths()["stdlib"]
    assert not is_in_app(str(Path(stdlib) / "json" / "decoder.py"))


def test_the_sdk_itself_is_not_in_app() -> None:
    """Otherwise an exception raised inside the SDK would be reported as an
    application bug, and would fingerprint on our file paths."""
    import roottrace_sdk._frames as frames_module

    assert not is_in_app(frames_module.__file__)


@pytest.mark.parametrize("filename", ["", "<stdin>", "<string>", "<frozen importlib._bootstrap>"])
def test_synthesised_frames_are_not_in_app(filename: str) -> None:
    assert not is_in_app(filename)


def test_an_include_prefix_forces_in_app() -> None:
    """A service installed into site-packages — an editable install, or a
    packaged deployment — is still the customer's code."""
    vendored = str(Path("/srv/.venv/lib/python3.12/site-packages/checkout/services.py"))
    assert not is_in_app(vendored)
    assert is_in_app(vendored, include=("/srv/.venv/lib/python3.12/site-packages/checkout",))


# ── The frame list ─────────────────────────────────────────────────────────


def test_frames_carry_file_line_function_and_in_app() -> None:
    frames = parse_frames(_raise_through_two_frames())

    assert len(frames) >= 3
    deepest = frames[-1]
    assert deepest["function"] == "inner"
    assert deepest["file"].endswith("test_frames.py")
    assert isinstance(deepest["line"], int) and deepest["line"] > 0
    assert deepest["in_app"] is True


def test_the_context_line_is_the_failing_source() -> None:
    frames = parse_frames(_raise_through_two_frames())
    assert frames[-1]["context_line"] == "return value + None  # type: ignore[operator]"


def test_surrounding_context_is_included_and_bounded() -> None:
    frames = parse_frames(_raise_through_two_frames(), context_lines=2)
    deepest = frames[-1]
    assert len(deepest.get("pre_context", [])) <= 2
    assert len(deepest.get("post_context", [])) <= 2
    assert any("def inner" in line for line in deepest["pre_context"])


def test_context_can_be_turned_off() -> None:
    deepest = parse_frames(_raise_through_two_frames(), context_lines=0)[-1]
    assert "pre_context" not in deepest
    assert "post_context" not in deepest


def test_the_column_is_reported_one_based_and_points_at_the_expression() -> None:
    """`line` is 1-based and `colno` is a 0-based offset. Reporting both the
    same way is what makes a `file:line:column` citation internally consistent
    — and citations are compared literally (P2).

    Asserted against the raw source rather than against `>= 1`, which the
    obvious `StackSummary.extract(walk_tb(...))` spelling would also satisfy by
    never emitting a column at all.
    """
    deepest = parse_frames(_raise_through_two_frames())[-1]
    raw = linecache.getlines(deepest["file"])[deepest["line"] - 1]

    assert raw[deepest["column"] - 1 :].startswith("value + None")


def test_truncation_keeps_the_deepest_frames() -> None:
    """A `RecursionError` is a thousand identical frames. Keeping the first N
    of those sends a payload containing nothing at all — the raise site, which
    is what retrieval and the fingerprint both read, is at the other end."""

    def recurse(depth: int) -> int:
        return recurse(depth + 1)

    try:
        recurse(0)
    except RecursionError as exc:
        frames = parse_frames(exc, max_frames=5)

    assert len(frames) == 5
    assert frames[-1]["function"] == "recurse"


# ── Locals ─────────────────────────────────────────────────────────────────


def _raise_with_a_secret_local() -> BaseException:
    def handler() -> None:
        password = "hunter2"
        cart_total = 49.99
        raise ValueError(f"boom {len(password)} {cart_total}")

    try:
        handler()
    except ValueError as exc:
        return exc
    raise AssertionError("expected a ValueError")


def test_locals_are_off_by_default() -> None:
    """`03` §S1 shows `vars` marked "// redacted", but redaction happens at
    ingest — by which time the value has already left the customer's process.
    `hunter2` is neither high-entropy nor pattern-shaped, so the server's rules
    would not catch it either."""
    for frame in parse_frames(_raise_with_a_secret_local()):
        assert "vars" not in frame


def test_opted_in_locals_redact_by_name() -> None:
    frames = parse_frames(_raise_with_a_secret_local(), capture_locals=True)
    variables = frames[-1]["vars"]

    assert variables["password"] == "[REDACTED:local_name]"
    assert "hunter2" not in str(variables)
    assert "49.99" in variables["cart_total"]


def test_a_huge_local_is_truncated() -> None:
    def handler() -> None:
        blob = "x" * 10_000
        raise ValueError(f"boom {len(blob)}")

    try:
        handler()
    except ValueError as exc:
        variables = parse_frames(exc, capture_locals=True)[-1]["vars"]

    assert len(variables["blob"]) <= 210


# ── The rest of the payload ────────────────────────────────────────────────


def test_the_stack_trace_is_the_human_readable_form() -> None:
    text = format_exception(_raise_through_two_frames())
    assert text.startswith("Traceback (most recent call last):")
    assert "TypeError" in text


def test_the_stack_trace_includes_a_chained_cause() -> None:
    """`raise B from A` — the cause is very often where the real fault is, and
    dropping it leaves the reader with the wrapper."""
    try:
        try:
            raise KeyError("tax_rate")
        except KeyError as cause:
            raise RuntimeError("could not price the cart") from cause
    except RuntimeError as exc:
        text = format_exception(exc)

    assert "KeyError" in text
    assert "RuntimeError" in text
    assert "direct cause" in text


def test_the_runtime_block_names_python_and_its_version() -> None:
    context: dict[str, Any] = runtime_context("fastapi", "0.111.0")
    assert context["language"] == "python"
    assert context["language_version"].startswith("3.12")
    assert context["framework"] == "fastapi"
    assert context["framework_version"] == "0.111.0"


def test_the_runtime_block_omits_the_framework_when_there_is_none() -> None:
    context = runtime_context()
    assert "framework" not in context
    assert "framework_version" not in context


def test_an_out_of_range_timestamp_is_refused() -> None:
    """UUIDv7 carries 48 bits of milliseconds. A clock reporting a year past
    10889 would silently wrap into a different id layout."""
    from roottrace_sdk._ids import uuid7

    with pytest.raises(ValueError, match="UUIDv7 range"):
        uuid7(now_ms=1 << 48)
