"""RootTrace AI Python SDK.

`init` · `capture_exception` · `add_breadcrumb` · FastAPI middleware, with
batching, retry, and a bounded local buffer (T2.5, `docs/15` §4; surface fixed
by `05` §10).

```python
import roottrace_sdk as roottrace

roottrace.init(
    api_key=os.environ["ROOTTRACE_API_KEY"],
    environment="production",
    service="checkout-api",
    release=os.environ.get("GIT_SHA"),
)

from roottrace_sdk.integrations.fastapi import RootTraceMiddleware
app.add_middleware(RootTraceMiddleware)

roottrace.add_breadcrumb(category="http", message="GET tax-service/rate → 503", level="warning")
```

**Never-raises guarantee.** This library runs inside someone else's production
request path. A failure to report an error must never become an error. Every
function below is wrapped by `_guard.never_raises`, which returns a default and
reports the failure to a sink instead of propagating it. The one place that
guarantee is deliberately *not* applied is the ASGI middleware's re-raise: the
SDK must not swallow the host application's own exception.
"""

from __future__ import annotations

import atexit
import random
import sys
from datetime import datetime
from typing import Any

from roottrace_sdk import _breadcrumbs, _guard
from roottrace_sdk._client import Client, register_fork_hook
from roottrace_sdk._config import Config, ConfigError, build
from roottrace_sdk._event import build_event
from roottrace_sdk._version import __version__

__all__ = [
    "__version__",
    "add_breadcrumb",
    "capture_exception",
    "close",
    "flush",
    "init",
    "is_initialised",
]

# `_active_*`, not `_config` / `_client`. Those names are the submodules
# `_config.py` and `_client.py`, and a module-level variable of the same name
# shadows the submodule on the package object: `from roottrace_sdk import
# _client` would then bind `None` rather than the module.
_active_config: Config | None = None
_active_client: Client | None = None
_atexit_registered = False

#: Not `secrets`. Sampling is a load-shedding decision, not a security one, and
#: `secrets` would spend a syscall's worth of entropy per captured exception.
_rng = random.Random()  # noqa: S311 - see above


def init(api_key: str | None = None, **options: Any) -> bool:
    """Configure the SDK. Returns True if it is now reporting.

    Configuration mistakes are reported to stderr and disable reporting rather
    than raising: an SDK that crashes an application at import time because a
    key was mistyped has done more damage than the missing telemetry.
    """
    global _active_config, _active_client, _atexit_registered

    previous = _active_client
    try:
        config = build(api_key=api_key, **options)
        _guard.set_debug(config.debug)
        client = Client(config)
    except ConfigError as exc:
        _guard.warn(f"init failed, error reporting is disabled: {exc}")
        _active_config, _active_client = None, None
        return False
    except Exception as exc:  # an odd option type, a transport that refuses
        _guard.report("init", exc)
        _guard.warn("init failed, error reporting is disabled")
        _active_config, _active_client = None, None
        return False

    _active_config, _active_client = config, client

    if previous is not None:
        # Re-initialising must not strand whatever the old client had buffered.
        with _guard.suppressed("init.close_previous"):
            previous.close(timeout=1.0)

    if not _atexit_registered:
        atexit.register(_at_exit)
        register_fork_hook(lambda: _active_client)
        _atexit_registered = True

    return True


def is_initialised() -> bool:
    return _active_client is not None


@_guard.never_raises("capture_exception", None)
def capture_exception(
    exc: BaseException | None = None,
    *,
    level: str = "error",
    tags: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
    user_context: dict[str, Any] | None = None,
    framework: str | None = None,
    framework_version: str | None = None,
    now: datetime | None = None,
) -> str | None:
    """Capture an exception. Returns its `event_id`, or None if not sent.

    Called with no argument inside an `except` block, it captures the exception
    currently being handled — which is how `05` §10's example uses it.
    """
    config, client = _active_config, _active_client
    if config is None or client is None:
        return None

    if exc is None:
        exc = sys.exc_info()[1]
    if exc is None:
        return None

    if config.sample_rate < 1.0 and _rng.random() >= config.sample_rate:
        return None

    event = build_event(
        exc,
        config=config,
        breadcrumbs=_breadcrumbs.snapshot(),
        level=level,
        tags=tags,
        extra=extra,
        request=request,
        user_context=user_context,
        framework=framework,
        framework_version=framework_version,
        now=now,
    )

    if config.before_send is not None:
        # Customer code, run inside our guard. A `before_send` that raises
        # drops the event; it does not take down the request.
        filtered = config.before_send(event)
        if not filtered:
            return None
        event = filtered

    client.capture(event)
    event_id = event.get("event_id")
    return str(event_id) if event_id else None


@_guard.never_raises("add_breadcrumb", None)
def add_breadcrumb(
    *,
    category: str,
    message: str,
    level: str = "info",
    data: dict[str, Any] | None = None,
    ts: datetime | None = None,
) -> None:
    """Record something that happened before the error (`03` §S1).

    Works whether or not `init` has been called — the trail is per-context and
    costs nothing until an event is built. Dropping breadcrumbs recorded before
    a late `init` would lose exactly the early-startup ones.
    """
    config = _active_config
    _breadcrumbs.add(
        category=category,
        message=message,
        level=level,
        data=data,
        ts=ts,
        max_breadcrumbs=config.max_breadcrumbs if config else 25,
    )


@_guard.never_raises("flush", False)
def flush(timeout: float = 2.0) -> bool:
    """Send everything buffered. True if the buffer emptied in time."""
    client = _active_client
    return True if client is None else client.flush(timeout)


@_guard.never_raises("close", None)
def close(timeout: float = 2.0) -> None:
    """Flush and stop the sender. Safe to call more than once."""
    global _active_client, _active_config
    client = _active_client
    _active_client, _active_config = None, None
    if client is not None:
        client.close(timeout)


def _at_exit() -> None:
    with _guard.suppressed("atexit"):
        client = _active_client
        if client is not None:
            client.close(timeout=2.0)
