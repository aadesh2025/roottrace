"""The never-raises guarantee (T2.5, `05` §10).

> **never raises into the host application** — an observability SDK that can
> crash the app it observes is worse than no SDK.

**"It did not raise" is not evidence.** Code that never ran satisfies that
assertion just as well as code that ran and was guarded. So every test here
asserts both halves: the public call returned its documented default, *and* the
guard is what caught the failure — observed through a sink, with the seam
broken deliberately.

The last two tests are the ones that make the rest mean anything: a positive
control that the same call succeeds when nothing is broken, and a check that
`BaseException` still propagates.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

import roottrace_sdk
from roottrace_sdk import _breadcrumbs, _event, _guard
from roottrace_sdk import _client as client_module
from roottrace_sdk._transport import SendResult

pytestmark = pytest.mark.unit

TEST_API_KEY = "rt_test_" + "0" * 32
LOOPBACK = "http://127.0.0.1:1/v1/events"


class _Silent:
    def __init__(self, config: Any) -> None:
        self.config = config

    def send(self, events: list[dict[str, Any]], *, idempotency_key: str) -> SendResult:
        return SendResult(ok=True, retryable=False, status=202)


@pytest.fixture
def initialised(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(client_module, "HttpTransport", _Silent)
    assert roottrace_sdk.init(api_key=TEST_API_KEY, endpoint=LOOPBACK)
    yield


def explode(*args: object, **kwargs: object) -> Any:
    raise RuntimeError("a seam is broken")


def boom() -> BaseException:
    try:
        raise ValueError("the application's own error")
    except ValueError as exc:
        return exc


# ── One broken seam at a time ──────────────────────────────────────────────


def test_a_broken_event_builder_does_not_reach_the_caller(
    initialised: None, monkeypatch: pytest.MonkeyPatch, suppressed: list[tuple[str, BaseException]]
) -> None:
    monkeypatch.setattr(roottrace_sdk, "build_event", explode)

    assert roottrace_sdk.capture_exception(boom()) is None
    assert [where for where, _ in suppressed] == ["capture_exception"]


def test_a_broken_frame_parser_does_not_reach_the_caller(
    initialised: None, monkeypatch: pytest.MonkeyPatch, suppressed: list[tuple[str, BaseException]]
) -> None:
    monkeypatch.setattr(_event, "parse_frames", explode)

    assert roottrace_sdk.capture_exception(boom()) is None
    assert [where for where, _ in suppressed] == ["capture_exception"]


def test_a_before_send_that_raises_drops_the_event_only(
    monkeypatch: pytest.MonkeyPatch, suppressed: list[tuple[str, BaseException]]
) -> None:
    """`before_send` is customer code running inside a crashing request."""
    monkeypatch.setattr(client_module, "HttpTransport", _Silent)
    roottrace_sdk.init(api_key=TEST_API_KEY, endpoint=LOOPBACK, before_send=explode)

    assert roottrace_sdk.capture_exception(boom()) is None
    assert [where for where, _ in suppressed] == ["capture_exception"]


def test_a_broken_breadcrumb_trail_does_not_reach_the_caller(
    monkeypatch: pytest.MonkeyPatch, suppressed: list[tuple[str, BaseException]]
) -> None:
    monkeypatch.setattr(_breadcrumbs, "add", explode)

    assert roottrace_sdk.add_breadcrumb(category="db", message="query") is None
    assert [where for where, _ in suppressed] == ["add_breadcrumb"]


def test_a_broken_flush_returns_false_rather_than_raising(
    initialised: None, monkeypatch: pytest.MonkeyPatch, suppressed: list[tuple[str, BaseException]]
) -> None:
    """A host application calling `flush()` from a lifespan handler must not
    have its shutdown fail because ours did."""
    monkeypatch.setattr(client_module.Client, "flush", explode)

    assert roottrace_sdk.flush(timeout=0.1) is False
    assert [where for where, _ in suppressed] == ["flush"]


def test_a_broken_close_does_not_reach_the_caller(
    initialised: None, monkeypatch: pytest.MonkeyPatch, suppressed: list[tuple[str, BaseException]]
) -> None:
    monkeypatch.setattr(client_module.Client, "close", explode)

    assert roottrace_sdk.close(timeout=0.1) is None
    assert [where for where, _ in suppressed] == ["close"]


def test_a_bad_init_disables_reporting_without_raising(
    suppressed: list[tuple[str, BaseException]],
) -> None:
    assert roottrace_sdk.init(api_key="nonsense") is False
    assert roottrace_sdk.capture_exception(boom()) is None
    # Nothing was swallowed — a configuration error is reported through `warn`,
    # not through the guard, because the developer has to see it.
    assert suppressed == []


# ── What must still get through ────────────────────────────────────────────


def test_the_control_succeeds_when_nothing_is_broken(
    initialised: None, suppressed: list[tuple[str, BaseException]]
) -> None:
    """Without this, every assertion above is also satisfied by an SDK that
    does nothing at all."""
    assert roottrace_sdk.capture_exception(boom()) is not None
    assert suppressed == []


def test_keyboard_interrupt_is_not_swallowed(
    initialised: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`KeyboardInterrupt`, `SystemExit` and `asyncio.CancelledError` are
    control flow, not failure. Swallowing a `CancelledError` inside a
    middleware turns a cancelled request into a hung one — a worse outcome than
    the crash the guarantee exists to prevent."""

    def interrupt(*args: object, **kwargs: object) -> Any:
        raise KeyboardInterrupt

    monkeypatch.setattr(roottrace_sdk, "build_event", interrupt)

    with pytest.raises(KeyboardInterrupt):
        roottrace_sdk.capture_exception(boom())


def test_system_exit_is_not_swallowed(initialised: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def leave(*args: object, **kwargs: object) -> Any:
        raise SystemExit(3)

    monkeypatch.setattr(roottrace_sdk, "build_event", leave)

    with pytest.raises(SystemExit):
        roottrace_sdk.capture_exception(boom())


# ── The reporting machinery itself ─────────────────────────────────────────


def test_a_sink_that_raises_does_not_become_the_crash(
    initialised: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sinks are hostile by the same standard as everything else."""
    remove = _guard.add_sink(explode)
    monkeypatch.setattr(roottrace_sdk, "build_event", explode)
    try:
        assert roottrace_sdk.capture_exception(boom()) is None
    finally:
        remove()


def test_debug_mode_prints_what_it_swallowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A silent guard is a guard nobody can debug."""
    monkeypatch.setattr(client_module, "HttpTransport", _Silent)
    roottrace_sdk.init(api_key=TEST_API_KEY, endpoint=LOOPBACK, debug=True)
    monkeypatch.setattr(roottrace_sdk, "build_event", explode)

    roottrace_sdk.capture_exception(boom())

    captured = capsys.readouterr().err
    assert "capture_exception" in captured
    assert "a seam is broken" in captured


def test_nothing_is_printed_when_debug_is_off(
    initialised: None, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The other half. An SDK writing tracebacks to a customer's stderr on
    every capture is its own kind of failure."""
    monkeypatch.setattr(roottrace_sdk, "build_event", explode)

    roottrace_sdk.capture_exception(boom())

    assert capsys.readouterr().err == ""
