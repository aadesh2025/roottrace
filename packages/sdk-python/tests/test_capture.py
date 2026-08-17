"""The public surface — `init`, `capture_exception`, `add_breadcrumb`.

Fixed by `05` §10. The payload these produce is the contract in `03` §S1, and
`tests/integration/test_sdk_contract_agreement.py` puts it through the server's
own validator rather than trusting the shape asserted here.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

import roottrace_sdk
from roottrace_sdk import _client as client_module
from roottrace_sdk._transport import SendResult

pytestmark = pytest.mark.unit

TEST_API_KEY = "rt_test_" + "0" * 32
LOOPBACK = "http://127.0.0.1:1/v1/events"


class _Recording:
    """A transport with `HttpTransport`'s constructor signature."""

    instances: ClassVar[list[_Recording]] = []

    def __init__(self, config: Any) -> None:
        self.config = config
        self.calls: list[tuple[list[dict[str, Any]], str]] = []
        _Recording.instances.append(self)

    def send(self, events: list[dict[str, Any]], *, idempotency_key: str) -> SendResult:
        self.calls.append(([dict(event) for event in events], idempotency_key))
        return SendResult(ok=True, retryable=False, status=202)

    @property
    def sent(self) -> list[dict[str, Any]]:
        return [event for batch, _ in self.calls for event in batch]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Recording]:
    """`init` the SDK against a transport that records instead of sending."""
    _Recording.instances.clear()
    monkeypatch.setattr(client_module, "HttpTransport", _Recording)
    assert roottrace_sdk.init(api_key=TEST_API_KEY, endpoint=LOOPBACK, service="checkout-api")
    yield _Recording.instances[-1]


def boom() -> BaseException:
    def calculate_total(base_price: float, tax_amount: Any) -> Any:
        return base_price + tax_amount

    try:
        calculate_total(49.99, None)
    except TypeError as exc:
        return exc
    raise AssertionError("expected a TypeError")


def only_event(recording: _Recording) -> dict[str, Any]:
    assert roottrace_sdk.flush(timeout=5.0)
    assert len(recording.sent) == 1
    return recording.sent[0]


# ── The payload ────────────────────────────────────────────────────────────


def test_a_captured_exception_carries_the_documented_error_block(sent: _Recording) -> None:
    roottrace_sdk.capture_exception(boom())
    event = only_event(sent)

    assert event["error"]["type"] == "TypeError"
    assert "unsupported operand" in event["error"]["message"]
    assert event["error"]["stack_trace"].startswith("Traceback")
    assert event["error"]["stack_frames"][-1]["function"] == "calculate_total"


def test_the_event_carries_the_configured_identity(sent: _Recording) -> None:
    roottrace_sdk.capture_exception(boom())
    event = only_event(sent)

    assert event["environment"] == "production"
    assert event["service"] == "checkout-api"
    assert event["level"] == "error"
    assert event["event_id"].startswith("evt_")
    assert event["timestamp"].endswith("Z")


def test_capture_returns_the_event_id_it_sent(sent: _Recording) -> None:
    """So a caller can put it in a log line and find the event in the
    dashboard from the application's own logs."""
    returned = roottrace_sdk.capture_exception(boom())
    assert returned == only_event(sent)["event_id"]


def test_breadcrumbs_travel_with_the_event(sent: _Recording) -> None:
    """`03` §S1: "the 503 from the tax service is the actual root cause"."""
    roottrace_sdk.add_breadcrumb(category="db", message="SELECT cart WHERE id=? (12ms)")
    roottrace_sdk.add_breadcrumb(
        category="http", message="GET tax-service/rate → 503", level="warning"
    )
    roottrace_sdk.capture_exception(boom())

    crumbs = only_event(sent)["breadcrumbs"]
    assert [crumb["message"] for crumb in crumbs] == [
        "SELECT cart WHERE id=? (12ms)",
        "GET tax-service/rate → 503",
    ]


def test_tags_and_extra_are_attached(sent: _Recording) -> None:
    roottrace_sdk.capture_exception(boom(), tags={"region": "eu-west-1"}, extra={"items": 3})
    event = only_event(sent)

    assert event["tags"] == {"region": "eu-west-1"}
    assert event["extra"] == {"items": 3}


def test_an_unknown_level_falls_back_to_error(sent: _Recording) -> None:
    """`04` §7 fixes the enum; the server answers anything else with
    `RT-INGEST-0011`, and losing the event to a typo in a label is a bad
    trade."""
    roottrace_sdk.capture_exception(boom(), level="critical")
    assert only_event(sent)["level"] == "error"


def test_request_headers_are_allowlisted_before_leaving_the_process(sent: _Recording) -> None:
    """The server sanitises too (`03` §S1 step 6). Doing it here means an
    `Authorization` header never crosses the network at all — the only version
    of the control that holds if something logs the request in between."""
    roottrace_sdk.capture_exception(
        boom(),
        request={
            "method": "POST",
            "url": "/api/v2/checkout",
            "headers": {
                "content-type": "application/json",
                "Authorization": "Bearer rt_live_" + "f" * 32,
                "Cookie": "session=abc",
            },
        },
    )

    headers = only_event(sent)["request"]["headers"]
    assert headers == {"content-type": "application/json"}
    assert "rt_live_" not in str(only_event(sent))


# ── Sampling and filtering ─────────────────────────────────────────────────


def test_capture_with_no_argument_uses_the_exception_being_handled(sent: _Recording) -> None:
    """`05` §10's own example calls it bare inside an `except` block."""
    try:
        raise ValueError("inside a handler")
    except ValueError:
        roottrace_sdk.capture_exception()

    assert only_event(sent)["error"]["type"] == "ValueError"


def test_capture_outside_a_handler_with_no_argument_sends_nothing(sent: _Recording) -> None:
    assert roottrace_sdk.capture_exception() is None
    assert roottrace_sdk.flush(timeout=1.0)
    assert sent.sent == []


def test_zero_sample_rate_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    _Recording.instances.clear()
    monkeypatch.setattr(client_module, "HttpTransport", _Recording)
    roottrace_sdk.init(api_key=TEST_API_KEY, endpoint=LOOPBACK, sample_rate=0.0)

    assert roottrace_sdk.capture_exception(boom()) is None
    assert roottrace_sdk.flush(timeout=1.0)
    assert _Recording.instances[-1].sent == []


def test_before_send_can_drop_an_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """`05` §10's example: `None if e.error.type == "ClientDisconnect" else e`."""
    _Recording.instances.clear()
    monkeypatch.setattr(client_module, "HttpTransport", _Recording)
    roottrace_sdk.init(
        api_key=TEST_API_KEY,
        endpoint=LOOPBACK,
        before_send=lambda event: None if event["error"]["type"] == "TypeError" else event,
    )

    assert roottrace_sdk.capture_exception(boom()) is None
    assert roottrace_sdk.flush(timeout=1.0)
    assert _Recording.instances[-1].sent == []


def test_before_send_can_rewrite_an_event(monkeypatch: pytest.MonkeyPatch) -> None:
    _Recording.instances.clear()
    monkeypatch.setattr(client_module, "HttpTransport", _Recording)

    def scrub(event: dict[str, Any]) -> dict[str, Any]:
        event["error"]["message"] = "[scrubbed]"
        return event

    roottrace_sdk.init(api_key=TEST_API_KEY, endpoint=LOOPBACK, before_send=scrub)
    roottrace_sdk.capture_exception(boom())

    assert roottrace_sdk.flush(timeout=5.0)
    assert _Recording.instances[-1].sent[0]["error"]["message"] == "[scrubbed]"


# ── Lifecycle ──────────────────────────────────────────────────────────────


def test_capture_before_init_is_a_no_op_not_an_error() -> None:
    """An application that captures during import, before `init` runs, must not
    crash — and must not silently believe it is reporting."""
    roottrace_sdk.close(timeout=0.1)
    assert not roottrace_sdk.is_initialised()
    assert roottrace_sdk.capture_exception(boom()) is None


def test_breadcrumbs_work_before_init(sent: _Recording) -> None:
    """Recorded before `init` and still attached. Dropping them would lose
    exactly the early-startup crumbs, which are the ones nobody can reproduce."""
    roottrace_sdk.close(timeout=0.1)
    roottrace_sdk.add_breadcrumb(category="boot", message="loading config")

    roottrace_sdk.init(api_key=TEST_API_KEY, endpoint=LOOPBACK)
    roottrace_sdk.capture_exception(boom())
    assert roottrace_sdk.flush(timeout=5.0)

    crumbs = _Recording.instances[-1].sent[0]["breadcrumbs"]
    assert crumbs[0]["message"] == "loading config"


def test_a_bad_key_disables_reporting_and_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    assert roottrace_sdk.init(api_key="not-a-key") is False
    assert not roottrace_sdk.is_initialised()
    assert "api_key" in capsys.readouterr().err


def test_re_initialising_flushes_the_previous_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise whatever the old client had buffered is stranded in a thread
    nobody holds a reference to."""
    _Recording.instances.clear()
    monkeypatch.setattr(client_module, "HttpTransport", _Recording)
    roottrace_sdk.init(api_key=TEST_API_KEY, endpoint=LOOPBACK)
    roottrace_sdk.capture_exception(boom())

    roottrace_sdk.init(api_key=TEST_API_KEY, endpoint=LOOPBACK, service="second")

    assert len(_Recording.instances) == 2
    assert len(_Recording.instances[0].sent) == 1


def test_close_makes_the_sdk_uninitialised(sent: _Recording) -> None:
    assert roottrace_sdk.is_initialised()
    roottrace_sdk.close(timeout=1.0)
    assert not roottrace_sdk.is_initialised()


def test_the_public_surface_is_what_the_spec_names() -> None:
    """`05` §10 lists `init`, `capture_exception`, `add_breadcrumb` and the
    middleware. `flush`/`close`/`is_initialised` are the lifecycle the batching
    requires. Anything else appearing here is API surface nobody agreed to."""
    assert set(roottrace_sdk.__all__) == {
        "__version__",
        "add_breadcrumb",
        "capture_exception",
        "close",
        "flush",
        "init",
        "is_initialised",
    }


def test_an_exception_whose_str_raises_is_still_captured(sent: _Recording) -> None:
    """`__str__` is customer code, and it runs inside the capture path. An
    exception class with a broken one would otherwise turn a captured error
    into a second, uncaught one."""

    class HostileError(Exception):
        def __str__(self) -> str:
            raise RuntimeError("even the message is broken")

    try:
        raise HostileError
    except HostileError as exc:
        assert roottrace_sdk.capture_exception(exc) is not None

    event = only_event(sent)
    assert event["error"]["type"] == "HostileError"
    assert event["error"]["message"] == "<unprintable HostileError>"
