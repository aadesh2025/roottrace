"""The standard error envelope and its handlers.

`05` §2.3 and §3 define the shape; `17` §4 owns the code registry. Every error
path — ours, FastAPI's validation, Starlette's 404/405, and an unhandled
exception — leaves through here, so a client never sees two different error
shapes from the same API.

Codes are looked up, never composed. An unregistered code raises rather than
being emitted, which is the mechanical form of "don't invent unregistered
codes" (CLAUDE.md). `test_error_code_registry_matches_docs` compares this table
against `17` §4 in both directions, so a code added to one and not the other is
a failing test rather than drift discovered months later.
"""

from __future__ import annotations

from typing import Any, Final, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from roottrace_api.context import current_request_id
from roottrace_api.log import get_logger

logger = get_logger(__name__)

DOCUMENTATION_BASE: Final = "https://docs.roottrace.ai/errors"

#: `17` §4, restricted to the codes that map to an HTTP status. The registry
#: also holds codes whose HTTP column is "—" (pipeline terminal states, boot
#: failures); those are outcomes recorded in the database, never responses, and
#: including them here would invite emitting one.
ERROR_CODES: Final[dict[str, int]] = {
    # Authentication & authorisation
    "RT-AUTH-0001": 401,
    "RT-AUTH-0002": 401,
    "RT-AUTH-0003": 403,
    "RT-AUTH-0004": 401,
    "RT-AUTH-0005": 403,
    "RT-AUTH-0006": 403,
    "RT-AUTH-0007": 401,
    "RT-AUTH-0008": 401,
    "RT-AUTH-0020": 401,
    "RT-AUTH-0030": 409,
    "RT-AUTH-0031": 403,
    # Ingestion
    "RT-INGEST-0003": 400,
    "RT-INGEST-0004": 413,
    "RT-INGEST-0010": 422,
    "RT-INGEST-0011": 422,
    "RT-INGEST-0012": 422,
    "RT-INGEST-0013": 422,
    "RT-INGEST-0014": 422,
    # Validation, rate, quota
    "RT-VALIDATION-0001": 422,
    "RT-VALIDATION-0002": 405,
    "RT-RATE-0001": 429,
    "RT-RATE-0002": 429,
    "RT-QUOTA-0001": 402,
    "RT-QUOTA-0002": 402,
    # Resources
    "RT-NOTFOUND-0001": 404,
    "RT-NOTFOUND-0002": 404,
    "RT-CONFLICT-0001": 409,
    "RT-CONFLICT-0002": 409,
    "RT-CONFLICT-0003": 409,
    "RT-CONFLICT-0004": 409,
    # Pipeline
    "RT-PIPELINE-0001": 500,
    "RT-PIPELINE-0007": 504,
    "RT-PIPELINE-0008": 500,
    # AI
    "RT-AI-0001": 502,
    "RT-AI-0002": 502,
    "RT-AI-0003": 500,
    "RT-AI-0004": 500,
    "RT-AI-0005": 500,
    "RT-AI-0006": 500,
    "RT-AI-0007": 400,
    # GitHub
    "RT-GITHUB-0001": 502,
    "RT-GITHUB-0002": 403,
    "RT-GITHUB-0003": 409,
    "RT-GITHUB-0004": 404,
    "RT-GITHUB-0005": 403,
    "RT-GITHUB-0006": 422,
    "RT-GITHUB-0007": 403,
    "RT-GITHUB-0008": 401,
    # Sandbox
    "RT-SANDBOX-0001": 500,
    "RT-SANDBOX-0002": 504,
    "RT-SANDBOX-0003": 500,
    "RT-SANDBOX-0004": 503,
    "RT-SANDBOX-0005": 500,
    # Internal
    "RT-INTERNAL-0001": 500,
    "RT-INTERNAL-0002": 503,
    "RT-INTERNAL-0003": 503,
    "RT-INTERNAL-0004": 500,
    "RT-INTERNAL-0005": 500,
}


class ApiError(Exception):
    """A failure with a registered code, raised anywhere in a request.

    `message` is user-facing. It must not carry internal detail — a message is
    a disclosure channel, and the whole point of `request_id` is that an
    operator can find everything else in the logs without it being in the body.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise KeyError(f"{code} is not in the error registry (`17` §4)")
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    @property
    def status_code(self) -> int:
        return ERROR_CODES[self.code]


def error_response(
    code: str,
    message: str,
    *,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build the `05` §3 error envelope. The single place one is constructed."""
    if code not in ERROR_CODES:
        raise KeyError(f"{code} is not in the error registry (`17` §4)")

    body: dict[str, Any] = {"code": code, "message": message}
    if details:
        body["details"] = details
    body["request_id"] = current_request_id()
    body["documentation_url"] = f"{DOCUMENTATION_BASE}/{code}"

    return JSONResponse(
        status_code=ERROR_CODES[code],
        content={"error": body},
        headers=headers,
    )


# ── Handlers ───────────────────────────────────────────────────────────────
#
# Starlette types every handler as taking `Exception`, so each one narrows with
# `cast`. Not `assert isinstance` — `python -O` strips asserts, and a guard that
# disappears under an optimisation flag is not a guard. Here the cast is purely
# a typing artefact: Starlette dispatches on the registered class, so the
# runtime type is already correct.


async def handle_api_error(_request: Request, exc: Exception) -> JSONResponse:
    error = cast(ApiError, exc)
    return error_response(error.code, error.message, details=error.details)


async def handle_validation_error(_request: Request, exc: Exception) -> JSONResponse:
    """FastAPI's request validation, mapped onto `05` §3's `details` array.

    `loc` is joined into a dotted path so the field names a client sees match
    the ones in its own request body.
    """
    validation_error = cast(RequestValidationError, exc)
    details = [
        {
            # `loc` starts with the source ("body", "query"); drop it, the
            # client knows what it sent.
            "field": ".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][0]),
            "code": str(error["type"]),
            "message": str(error["msg"]),
        }
        for error in validation_error.errors()
    ]
    return error_response("RT-VALIDATION-0001", "Request validation failed", details=details)


#: Starlette raises bare HTTPExceptions for routing failures, which carry a
#: status and no code. Mapped rather than passed through, so those responses
#: keep the envelope too.
_STATUS_TO_CODE: Final[dict[int, str]] = {
    401: "RT-AUTH-0001",
    403: "RT-AUTH-0003",
    404: "RT-NOTFOUND-0001",
    405: "RT-VALIDATION-0002",
    409: "RT-CONFLICT-0001",
    413: "RT-INGEST-0004",
    422: "RT-VALIDATION-0001",
    429: "RT-RATE-0001",
}


async def handle_http_exception(_request: Request, exc: Exception) -> JSONResponse:
    """`HTTPException`, whether ours or Starlette's own.

    A handler that raises `HTTPException(detail={"code": ..., "message": ...})`
    keeps its code; anything else is mapped by status.
    """
    http_exc = cast(StarletteHTTPException, exc)
    headers = dict(http_exc.headers) if http_exc.headers else None

    # Starlette annotates `detail` as `str`; FastAPI's subclass accepts any
    # JSON-serialisable value, and our auth dependency passes a dict. Widened
    # here rather than narrowed at the raiser, so a handler cannot lose its
    # code by raising the Starlette class.
    detail: Any = http_exc.detail
    if isinstance(detail, dict) and "code" in detail:
        code = str(detail["code"])
        message = str(detail.get("message", ""))
        return error_response(code, message, headers=headers)

    code = _STATUS_TO_CODE.get(http_exc.status_code, "RT-INTERNAL-0001")
    return error_response(code, str(detail), headers=headers)


def register_error_handlers(app: FastAPI) -> None:
    """Handlers for failures a route raises deliberately.

    `Exception` is deliberately **not** registered. FastAPI routes that handler
    to Starlette's `ServerErrorMiddleware`, which sits outside every user
    middleware — so its response carries no `X-Request-ID` and no security
    headers, and it runs after the request-id contextvar has been reset, making
    the envelope's own `request_id` null. `RequestContextMiddleware` catches
    unhandled exceptions instead, inside the stack that decorates them.
    """
    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
