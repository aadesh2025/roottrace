"""The ASGI middleware (T2.5, `05` §10).

Driven as raw ASGI rather than through a `TestClient`, for the same reason the
middleware is written as raw ASGI: the SDK declares no dependencies, and a unit
test that needs Starlette installed to exercise it would quietly make that
untrue. The FastAPI end-to-end run is
`tests/integration/test_sdk_end_to_end.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any, ClassVar

import pytest

import roottrace_sdk
from roottrace_sdk import _client as client_module
from roottrace_sdk._transport import SendResult
from roottrace_sdk.integrations.fastapi import RootTraceMiddleware, route_pattern

pytestmark = pytest.mark.unit

TEST_API_KEY = "rt_test_" + "0" * 32
LOOPBACK = "http://127.0.0.1:1/v1/events"


class _Recording:
    instances: ClassVar[list[_Recording]] = []

    def __init__(self, config: Any) -> None:
        self.calls: list[list[dict[str, Any]]] = []
        _Recording.instances.append(self)

    def send(self, events: list[dict[str, Any]], *, idempotency_key: str) -> SendResult:
        self.calls.append([dict(event) for event in events])
        return SendResult(ok=True, retryable=False, status=202)

    @property
    def sent(self) -> list[dict[str, Any]]:
        return [event for batch in self.calls for event in batch]


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Recording]:
    _Recording.instances.clear()
    monkeypatch.setattr(client_module, "HttpTransport", _Recording)
    assert roottrace_sdk.init(api_key=TEST_API_KEY, endpoint=LOOPBACK, service="checkout-api")
    yield _Recording.instances[-1]


def http_scope(**overrides: Any) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "POST",
        "path": "/api/v2/checkout/8823",
        "query_string": b"",
        "headers": [],
    }
    scope.update(overrides)
    return scope


async def _receive() -> dict[str, Any]:  # pragma: no cover - never awaited here
    return {"type": "http.request"}


async def _send(message: dict[str, Any]) -> None:
    return None


def run(app: Any, scope: dict[str, Any]) -> None:
    asyncio.run(app(scope, _receive, _send))


class _Route:
    def __init__(self, path: str) -> None:
        self.path = path


# ── The behaviour that must not change ─────────────────────────────────────


def test_the_application_exception_still_propagates(sent: _Recording) -> None:
    """The one place the never-raises guarantee does not apply, and must not.

    Swallowing the host application's own exception would turn a 500 into a
    hung request and hide the very failure being reported.
    """

    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise ValueError("checkout failed")

    with pytest.raises(ValueError, match="checkout failed"):
        run(RootTraceMiddleware(app), http_scope())


def test_a_successful_request_captures_nothing(sent: _Recording) -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})

    run(RootTraceMiddleware(app), http_scope())

    assert roottrace_sdk.flush(timeout=1.0)
    assert sent.sent == []


def test_a_non_http_scope_passes_straight_through(sent: _Recording) -> None:
    """WebSocket and lifespan scopes have no request to describe, and a
    lifespan handler raising during startup must not be reported as an HTTP
    error with a synthesised 500."""
    seen: list[str] = []

    async def app(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    run(RootTraceMiddleware(app), {"type": "lifespan"})

    assert seen == ["lifespan"]


# ── What it captures ───────────────────────────────────────────────────────


def test_an_unhandled_exception_is_captured_with_its_request(sent: _Recording) -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise ValueError("checkout failed")

    with pytest.raises(ValueError):
        run(RootTraceMiddleware(app), http_scope(route=_Route("/api/v2/checkout/{order_id}")))

    assert roottrace_sdk.flush(timeout=5.0)
    (event,) = sent.sent
    assert event["error"]["type"] == "ValueError"
    assert event["request"]["method"] == "POST"
    assert event["request"]["url"] == "/api/v2/checkout/8823"
    assert event["request"]["status_code"] == 500
    assert event["request"]["duration_ms"] >= 0
    assert event["runtime"]["framework"] == "fastapi"


def test_the_route_pattern_is_the_template_not_the_path() -> None:
    """`03` §S1: "pre-templated; avoids ID cardinality". Without it every
    order id becomes its own issue and the dashboard is unusable."""
    assert route_pattern(http_scope(route=_Route("/api/v2/checkout/{id}"))) == (
        "/api/v2/checkout/{id}"
    )


def test_a_missing_route_costs_a_signal_not_the_event() -> None:
    """`scope["route"]` is a framework internal. Reading it defensively means a
    Starlette change degrades grouping rather than dropping the report."""
    assert route_pattern(http_scope()) is None
    assert route_pattern(http_scope(route=object())) is None


def test_headers_are_allowlisted(sent: _Recording) -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise ValueError("boom")

    scope = http_scope(
        headers=[
            (b"content-type", b"application/json"),
            (b"authorization", b"Bearer rt_live_" + b"f" * 32),
            (b"cookie", b"session=abc"),
        ]
    )
    with pytest.raises(ValueError):
        run(RootTraceMiddleware(app), scope)

    assert roottrace_sdk.flush(timeout=5.0)
    (event,) = sent.sent
    assert event["request"]["headers"] == {"content-type": "application/json"}
    assert "rt_live_" not in str(event)
    assert "session=abc" not in str(event)


def test_a_credential_in_the_query_string_is_redacted(sent: _Recording) -> None:
    """`03` §S1 sends `query_params`, and a token in a query string is the
    oldest way to leak a credential into telemetry."""

    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise ValueError("boom")

    scope = http_scope(query_string=b"coupon=SAVE20&access_token=abc123&api_key=zzz")
    with pytest.raises(ValueError):
        run(RootTraceMiddleware(app), scope)

    assert roottrace_sdk.flush(timeout=5.0)
    params = sent.sent[0]["request"]["query_params"]
    assert params["coupon"] == "SAVE20"
    assert params["access_token"] == "[REDACTED:query_param]"
    assert params["api_key"] == "[REDACTED:query_param]"


def test_no_body_sample_is_read(sent: _Recording) -> None:
    """Reading it would mean draining and replaying `receive`, buffering every
    request body whether or not it fails — a cost imposed on the healthy path
    to improve the failing one."""

    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        run(RootTraceMiddleware(app), http_scope())

    assert roottrace_sdk.flush(timeout=5.0)
    assert "body_sample" not in sent.sent[0]["request"]


# ── Breadcrumbs ────────────────────────────────────────────────────────────


def test_breadcrumbs_recorded_during_the_request_are_attached(sent: _Recording) -> None:
    async def app(scope: Any, receive: Any, send: Any) -> None:
        roottrace_sdk.add_breadcrumb(category="http", message="GET tax-service/rate → 503")
        raise ValueError("boom")

    with pytest.raises(ValueError):
        run(RootTraceMiddleware(app), http_scope())

    assert roottrace_sdk.flush(timeout=5.0)
    crumbs = sent.sent[0]["breadcrumbs"]
    assert [crumb["message"] for crumb in crumbs] == ["GET tax-service/rate → 503"]


def test_concurrent_requests_do_not_share_breadcrumbs(sent: _Recording) -> None:
    """The failure this prevents is a report that confidently names a database
    call made on behalf of a different request.

    A barrier forces the interleaving rather than hoping for it.
    """
    barrier = asyncio.Barrier(2)

    async def app(scope: Any, receive: Any, send: Any) -> None:
        name = scope["path"]
        roottrace_sdk.add_breadcrumb(category="db", message=f"{name}-first")
        await barrier.wait()
        roottrace_sdk.add_breadcrumb(category="db", message=f"{name}-second")
        raise ValueError("boom")

    middleware = RootTraceMiddleware(app)

    async def both() -> None:
        async def one(path: str) -> None:
            with pytest.raises(ValueError):
                await middleware(http_scope(path=path), _receive, _send)

        await asyncio.gather(one("/a"), one("/b"))

    asyncio.run(both())

    assert roottrace_sdk.flush(timeout=5.0)
    trails = {
        event["request"]["url"]: [crumb["message"] for crumb in event["breadcrumbs"]]
        for event in sent.sent
    }
    assert trails == {
        "/a": ["/a-first", "/a-second"],
        "/b": ["/b-first", "/b-second"],
    }


def test_the_middleware_survives_an_uninitialised_sdk() -> None:
    """`add_middleware` runs at import time; `init` may not have been called
    yet, or may have failed. Neither may break the application."""
    roottrace_sdk.close(timeout=0.1)

    async def app(scope: Any, receive: Any, send: Any) -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        run(RootTraceMiddleware(app), http_scope())
