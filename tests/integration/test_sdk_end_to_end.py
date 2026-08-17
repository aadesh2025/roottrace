"""T2.5's acceptance criteria, both halves, against real sockets.

> **Accept:** A demo FastAPI app throws an exception → the event arrives with
> parsed frames and breadcrumbs. Killing the API mid-run causes buffering, not
> a crash in the host app.

Neither half can be proved with a fake transport. The first needs a real
FastAPI application, a real ASGI stack, and a real HTTP round trip — the SDK's
middleware reads `scope["route"]`, which only exists because Starlette put it
there. The second needs a listener that genuinely stops listening: a stubbed
transport returning "unreachable" tests our handling of a value we invented,
not our handling of a closed socket.

No `sleep()` anywhere. The sender is woken by `flush()` and reports back
through a `Condition`, so every wait here is on a real event.
"""

from __future__ import annotations

import gzip
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

import roottrace_sdk
from roottrace_sdk.integrations.fastapi import RootTraceMiddleware

pytestmark = pytest.mark.integration

TEST_API_KEY = "rt_test_" + "0" * 32


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        if self.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        body = json.loads(raw)
        server: Any = self.server
        with server.lock:
            server.batches.append(
                {
                    "events": body["events"],
                    "authorization": self.headers.get("Authorization"),
                    "idempotency_key": self.headers.get("Idempotency-Key"),
                }
            )
        payload = json.dumps({"data": {"accepted": len(body["events"]), "rejected": 0}}).encode()
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: Any) -> None:
        return None


class Api:
    """A stand-in for `POST /v1/events` that can be killed and brought back."""

    def __init__(self, port: int = 0) -> None:
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.batches: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.port = port

    def start(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", self.port), _Handler)
        server.batches = self.batches  # type: ignore[attr-defined]
        server.lock = self.lock  # type: ignore[attr-defined]
        self.port = server.server_address[1]
        self._server = server
        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()

    def kill(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        if self._thread is not None:
            self._thread.join(5.0)
            self._thread = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1/events"

    @property
    def events(self) -> list[dict[str, Any]]:
        with self.lock:
            return [event for batch in self.batches for event in batch["events"]]


@pytest.fixture
def api() -> Iterator[Api]:
    server = Api()
    server.start()
    try:
        yield server
    finally:
        server.kill()


@pytest.fixture(autouse=True)
def _close_sdk() -> Iterator[None]:
    yield
    roottrace_sdk.close(timeout=2.0)


def settled(timeout: float = 20.0) -> bool:
    """Block until the sender finishes whatever round it is in.

    `flush()` answers "did the buffer empty", which is False while the API is
    down and says nothing about *where* the events are. This waits on the
    sender's own condition instead — so the buffer can be inspected at a moment
    when the batch is definitely not in flight, with no polling and no sleep.
    """
    client = roottrace_sdk._active_client
    assert client is not None
    with client._drained:
        return client._drained.wait_for(lambda: not client._sending, timeout)


def demo_app() -> Any:
    """The demo FastAPI application T2.5 asks for.

    `/checkout/{order_id}` records a breadcrumb naming a failing dependency and
    then raises the downstream symptom — which is `03` §S1's own worked example
    of why breadcrumbs are worth more than the traceback.
    """
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/checkout/{order_id}")
    def checkout(order_id: str) -> dict[str, str]:
        roottrace_sdk.add_breadcrumb(category="db", message="SELECT cart WHERE id=? (12ms)")
        roottrace_sdk.add_breadcrumb(
            category="http", message="GET tax-service/rate → 503", level="warning"
        )
        return calculate_total(49.99, tax_rate_for(order_id))

    def tax_rate_for(order_id: str) -> Any:
        return None

    def calculate_total(base_price: float, tax_amount: Any) -> dict[str, str]:
        return {"total": str(base_price + tax_amount)}

    app.add_middleware(RootTraceMiddleware)
    return app


# ── The first criterion ────────────────────────────────────────────────────


def test_a_demo_fastapi_app_that_throws_delivers_frames_and_breadcrumbs(api: Api) -> None:
    """T2.5 verbatim."""
    from starlette.testclient import TestClient

    assert roottrace_sdk.init(
        api_key=TEST_API_KEY,
        endpoint=api.url,
        environment="production",
        service="checkout-api",
        release="v2.14.3",
        flush_interval=30.0,
    )

    with TestClient(demo_app(), raise_server_exceptions=False) as client:
        response = client.get("/checkout/8823")

    # The host application answered — the middleware re-raised into FastAPI's
    # own handler rather than swallowing the error or crashing the worker.
    assert response.status_code == 500

    assert roottrace_sdk.flush(timeout=10.0), "the event never reached the API"
    (event,) = api.events

    assert event["error"]["type"] == "TypeError"
    assert "unsupported operand" in event["error"]["message"]

    # Parsed frames, in-app, naming the function that failed.
    frames = event["error"]["stack_frames"]
    deepest = frames[-1]
    assert deepest["function"] == "calculate_total"
    assert deepest["in_app"] is True
    assert deepest["line"] > 0
    assert "base_price + tax_amount" in deepest["context_line"]

    # And the breadcrumb that is the actual root cause (`03` §S1).
    assert [crumb["message"] for crumb in event["breadcrumbs"]] == [
        "SELECT cart WHERE id=? (12ms)",
        "GET tax-service/rate → 503",
    ]

    assert event["service"] == "checkout-api"
    assert event["release"] == "v2.14.3"
    assert event["request"]["route_pattern"] == "/checkout/{order_id}"
    assert event["request"]["url"] == "/checkout/8823"


def test_the_batch_arrives_authenticated_and_idempotent(api: Api) -> None:
    """`05` §5's request contract: a bearer key and an `Idempotency-Key`."""
    assert roottrace_sdk.init(api_key=TEST_API_KEY, endpoint=api.url, flush_interval=30.0)

    try:
        raise ValueError("boom")
    except ValueError as exc:
        roottrace_sdk.capture_exception(exc)

    assert roottrace_sdk.flush(timeout=10.0)
    (batch,) = api.batches
    assert batch["authorization"] == f"Bearer {TEST_API_KEY}"
    assert batch["idempotency_key"]


# ── The second criterion ───────────────────────────────────────────────────


def test_killing_the_api_buffers_and_never_touches_the_host_app(api: Api) -> None:
    """T2.5 verbatim: "Killing the API mid-run causes buffering, not a crash in
    the host app."

    Three things are asserted, and all three matter:

    1. the application still answers every request while the API is down;
    2. the events are **buffered**, not discarded — discarding would pass a
       test that only checked the application survived;
    3. when the API comes back, the buffered events arrive.
    """
    from starlette.testclient import TestClient

    assert roottrace_sdk.init(
        api_key=TEST_API_KEY,
        endpoint=api.url,
        flush_interval=30.0,
        max_attempts=2,
        backoff_base=0.001,
        backoff_cap=0.01,
        # A connect to a dead loopback port is not always instant on Windows.
        # Bounding it keeps the retry schedule short without weakening what is
        # being tested — the socket is genuinely closed either way.
        timeout=0.5,
    )
    app = demo_app()

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/checkout/1").status_code == 500
        assert roottrace_sdk.flush(timeout=10.0)
        assert len(api.events) == 1

        port = api.port
        api.kill()

        # Mid-run: the application keeps serving, and keeps failing, with
        # nowhere to send the reports.
        for order in range(2, 6):
            assert client.get(f"/checkout/{order}").status_code == 500

        assert roottrace_sdk.flush(timeout=1.0) is False, "the API is down; this cannot succeed"
        assert settled(), "the sender never finished its retry schedule"

        buffered = roottrace_sdk._active_client
        assert buffered is not None
        assert len(buffered.buffer) == 4, "events were discarded rather than buffered"
        assert buffered.buffer.dropped == 0
        assert len(api.events) == 1, "an event reached an API that is not running"

        api.port = port
        api.start()

        assert roottrace_sdk.flush(timeout=20.0), "the buffer never drained after recovery"
        assert client.get("/checkout/6").status_code == 500

    assert roottrace_sdk.flush(timeout=10.0)
    received = api.events
    assert len(received) == 6
    assert len({event["event_id"] for event in received}) == 6, "an event was delivered twice"


def test_an_api_that_was_never_reachable_does_not_break_startup() -> None:
    """The worst case for a customer: the endpoint is wrong, or the network
    blocks it, and they find out from our stderr rather than from an outage."""
    assert roottrace_sdk.init(
        api_key=TEST_API_KEY,
        endpoint="http://127.0.0.1:1/v1/events",
        flush_interval=30.0,
        max_attempts=1,
        backoff_base=0.001,
        timeout=0.5,
    )

    try:
        raise ValueError("boom")
    except ValueError as exc:
        assert roottrace_sdk.capture_exception(exc) is not None

    assert roottrace_sdk.flush(timeout=1.0) is False
    assert settled()
    client = roottrace_sdk._active_client
    assert client is not None and len(client.buffer) == 1

    # And shutting down does not hang or raise, even with the event stranded.
    roottrace_sdk.close(timeout=1.0)
    assert not roottrace_sdk.is_initialised()
