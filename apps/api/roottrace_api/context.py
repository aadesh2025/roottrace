"""Per-request context.

Its own module so that `errors` and `middleware` can both reach the request id
without importing each other.
"""

from __future__ import annotations

from contextvars import ContextVar

#: Readable from anywhere inside a request, including exception handlers, which
#: run too deep to be handed a `Request` by us.
request_id_var: ContextVar[str | None] = ContextVar("rt_request_id", default=None)


def current_request_id() -> str | None:
    """The current request's id, or None outside a request."""
    return request_id_var.get()
