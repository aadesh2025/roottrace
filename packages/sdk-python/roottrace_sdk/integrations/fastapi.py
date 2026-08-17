"""FastAPI / Starlette integration (`05` §10).

```python
from roottrace_sdk.integrations.fastapi import RootTraceMiddleware
app.add_middleware(RootTraceMiddleware)
```

**Pure ASGI, with no import of `fastapi` or `starlette`.** `add_middleware`
only needs a callable class taking `app` and implementing `__call__(scope,
receive, send)`, so writing to the ASGI interface rather than to
`BaseHTTPMiddleware` keeps the SDK's dependency set empty — the customer's
FastAPI version is then irrelevant to us. It also avoids
`BaseHTTPMiddleware`'s well-known behaviour of buffering the response body,
which an error-reporting middleware has no business doing to a streaming
endpoint.

**It re-raises.** This is the one place the never-raises guarantee does not
apply, and it must not: swallowing the application's own exception would turn
a 500 into a hung request and hide the very failure being reported. We observe
and get out of the way.

**It starts a fresh breadcrumb trail per request**, which is what makes the
trail attached to an error belong to the request that failed rather than to
whichever concurrent request happened to log last.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qsl

from roottrace_sdk import _breadcrumbs, _guard, capture_exception
from roottrace_sdk._config import DEFAULT_MAX_BREADCRUMBS, HEADER_ALLOWLIST

Scope = dict[str, Any]

#: Query keys whose value is dropped before the payload is built. `03` §S1
#: sends `query_params`, and a token in a query string is the oldest way to
#: leak a credential into telemetry.
_SENSITIVE_QUERY = ("token", "secret", "password", "key", "signature", "code", "auth")


class RootTraceMiddleware:
    """Capture unhandled exceptions from an ASGI app.

    Only exceptions. A handler that catches its own error and returns a 500
    itself is **not** captured: there is no exception object to attach, and an
    event without `error.type` is rejected by `RT-INGEST-0011`. Guessing a type
    from a status code would produce a fingerprint that groups every unrelated
    500 in the service into one issue. Those call `capture_exception`
    explicitly.
    """

    def __init__(self, app: Any, *, max_breadcrumbs: int = DEFAULT_MAX_BREADCRUMBS) -> None:
        self.app = app
        self.max_breadcrumbs = max_breadcrumbs

    async def __call__(self, scope: Scope, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        with _guard.suppressed("middleware.begin"):
            # A NEW trail, not a `clear()`. Isolation between concurrent
            # requests comes from this `set` landing in the request task's own
            # copy of the context; mutating a shared deque would not isolate.
            _breadcrumbs.begin(self.max_breadcrumbs)

        started = time.perf_counter()

        try:
            await self.app(scope, receive, send)
        except Exception as exc:
            # An unhandled exception never produced a response, so the status
            # the client will see is the 500 the server synthesises.
            with _guard.suppressed("middleware.capture"):
                capture_exception(
                    exc,
                    request=request_context(scope, 500, _elapsed_ms(started)),
                    framework="fastapi",
                    framework_version=_framework_version(),
                )
            raise


def request_context(scope: Scope, status_code: int, duration_ms: int) -> dict[str, Any]:
    """`03` §S1's `request` block, built from an ASGI scope.

    `body_sample` is deliberately absent. Reading it would mean draining and
    replaying `receive`, which buffers the whole request body for every request
    whether or not it fails — a cost imposed on the healthy path to improve the
    failing one. Pass it explicitly to `capture_exception` where it is worth it.
    """
    context: dict[str, Any] = {
        "method": scope.get("method"),
        "url": scope.get("path"),
        "status_code": status_code,
        "duration_ms": duration_ms,
    }

    pattern = route_pattern(scope)
    if pattern:
        context["route_pattern"] = pattern

    headers = _headers(scope)
    if headers:
        context["headers"] = headers

    query = _query_params(scope)
    if query:
        context["query_params"] = query

    return context


def route_pattern(scope: Scope) -> str | None:
    """The templated path — `/api/v2/orders/{id}`, not `/api/v2/orders/8823`.

    `03` §S1 calls this "pre-templated; avoids ID cardinality", and it is what
    fingerprinting and `endpoint_criticality` both key on. Starlette merges the
    matched route into the scope, so the pattern is available even though the
    exception escaped the endpoint. Read defensively: it is a framework
    internal, and falling back to `None` costs a grouping signal rather than
    the event.
    """
    route = scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if isinstance(path, str) else None


def _headers(scope: Scope) -> dict[str, str]:
    """Allowlisted at the source — see `_event.normalise_request`.

    Applied twice on purpose. Here it means an `Authorization` header never
    enters the payload at all; the server's pass covers clients that are not
    this SDK.
    """
    collected: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers", []):
        name = raw_name.decode("latin-1").lower()
        if name in HEADER_ALLOWLIST:
            collected[name] = raw_value.decode("latin-1")
    return collected


def _query_params(scope: Scope) -> dict[str, str]:
    raw = scope.get("query_string") or b""
    params: dict[str, str] = {}
    for key, value in parse_qsl(raw.decode("latin-1")):
        lowered = key.lower()
        redacted = any(marker in lowered for marker in _SENSITIVE_QUERY)
        params[key] = "[REDACTED:query_param]" if redacted else value
    return params


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _framework_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("fastapi")
    except Exception:  # not installed, or a vendored copy with no metadata
        return None
