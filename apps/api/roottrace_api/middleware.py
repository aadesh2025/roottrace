"""Request context, the unhandled-exception boundary, and response headers.

Both middlewares are pure ASGI rather than Starlette's `BaseHTTPMiddleware`,
which wraps every request in an extra task and has long-standing trouble with
streaming responses and background tasks. Nothing here needs that machinery.
"""

from __future__ import annotations

import time
from typing import Final

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from roottrace_api.context import request_id_var
from roottrace_api.errors import error_response
from roottrace_api.ids import new_request_id
from roottrace_api.log import get_logger
from roottrace_api.settings import Settings

logger = get_logger("roottrace_api.request")

REQUEST_ID_HEADER: Final = "x-request-id"


class RequestContextMiddleware:
    """Mint a `request_id`, bind it to the log context, time the request, and
    be the boundary an unhandled exception cannot cross.

    **An inbound `X-Request-ID` is ignored, never adopted.** It is attacker-
    controlled text arriving on the public internet, and adopting it would put
    that text into the one field the entire operational story keys on: log
    correlation, the value quoted in every error body, and — from `12` §2.1 —
    the identifier propagated through the queue into pipeline steps. A caller
    that could choose it could forge collisions with another tenant's requests,
    or inject control characters into log lines. Upstream correlation is a real
    need and will be met by recording a client's value under a *separate* field
    when something needs it; it will not be met by trusting one.

    **The catch-all lives here rather than in a registered `Exception`
    handler.** FastAPI routes that handler to Starlette's `ServerErrorMiddleware`,
    which sits *outside* every user middleware: the response it produces never
    passes through the `send` wrappers below, so it arrives with no
    `X-Request-ID` and no security headers, and the request-id contextvar has
    already been reset by the time it runs — the envelope's `request_id` comes
    back null. All four defects, from one line of framework ordering. Catching
    here keeps the response inside the stack that decorates it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = new_request_id()
        token = request_id_var.set(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                headers.append((REQUEST_ID_HEADER.encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            # The traceback IS logged — an operator needs it — and the
            # redaction processor is what makes that safe. The client gets the
            # request id and nothing else: `str(exc)` on an unhandled failure
            # is routinely a connection string or a query with values inlined,
            # and this is the one path where nobody has decided what is safe to
            # say.
            logger.error(
                "unhandled_exception",
                method=scope.get("method"),
                path=scope.get("path"),
                exc_info=exc,
            )
            if response_started:
                # Headers are already on the wire; there is no envelope to
                # send. Let the server tear the connection down rather than
                # emit a body that contradicts the status already sent.
                raise
            response = error_response("RT-INTERNAL-0001", "An unexpected internal error occurred.")
            await response(scope, receive, send_with_request_id)
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            # One line per request, at the edge. `12` §2.2 forbids per-item
            # logging in hot loops; per-request at the boundary is the level
            # that stays useful at volume.
            logger.info(
                "http_request",
                method=scope.get("method"),
                path=scope.get("path"),
                status_code=status_code,
                duration_ms=duration_ms,
            )
            structlog.contextvars.unbind_contextvars("request_id")
            request_id_var.reset(token)


class SecurityHeadersMiddleware:
    """`11` §9 security headers on every response.

    Added last in `create_app`, which makes it the outermost user middleware,
    so it also decorates the envelope that `RequestContextMiddleware` produces
    for an unhandled exception — the response most likely to be missed.

    The CSP in `11` §9 is the **dashboard's**: it permits Monaco's WASM
    tokeniser and names image and connect sources a browser page needs. An API
    returning JSON needs none of that and sends the restrictive form instead. A
    copied-across CSP would grant this surface permissions it never uses.

    HSTS is sent only in staging and production. A browser that saw it from a
    local plain-HTTP deployment would pin `localhost` to HTTPS and refuse to
    load anything on it afterwards, including other projects.
    """

    def __init__(self, app: ASGIApp, *, settings: Settings) -> None:
        self.app = app
        headers: dict[bytes, bytes] = {
            b"x-content-type-options": b"nosniff",
            b"x-frame-options": b"DENY",
            b"referrer-policy": b"strict-origin-when-cross-origin",
            b"permissions-policy": b"geolocation=(), microphone=(), camera=(), payment=()",
            b"cross-origin-opener-policy": b"same-origin",
            b"content-security-policy": (
                b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
            ),
        }
        if settings.environment in ("staging", "production"):
            headers[b"strict-transport-security"] = b"max-age=63072000; includeSubDomains; preload"
        self.headers = tuple(headers.items())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self.headers)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
