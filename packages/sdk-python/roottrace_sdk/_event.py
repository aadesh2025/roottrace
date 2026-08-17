"""Assemble the event payload `03` §S1 specifies.

The shape is a contract, not a suggestion: `apps/api/roottrace_api/ingest/`
validates it field by field, `RT-INGEST-0011` names the required ones, and the
fixture corpus in `fixtures/corpus/` is compared against the same schema. A
field emitted under the wrong name is a rejected event, not a warning.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from roottrace_sdk._config import HEADER_ALLOWLIST, VALID_LEVELS, Config
from roottrace_sdk._frames import format_exception, parse_frames, runtime_context
from roottrace_sdk._ids import new_event_id
from roottrace_sdk._time import isoformat_ms


def build_event(
    exc: BaseException,
    *,
    config: Config,
    breadcrumbs: list[dict[str, Any]] | None = None,
    level: str = "error",
    tags: dict[str, str] | None = None,
    extra: dict[str, Any] | None = None,
    request: dict[str, Any] | None = None,
    user_context: dict[str, Any] | None = None,
    framework: str | None = None,
    framework_version: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event_id": new_event_id(),
        "timestamp": isoformat_ms(now or datetime.now(UTC)),
        "environment": config.environment,
        "level": level if level in VALID_LEVELS else "error",
        "error": {
            "type": type(exc).__name__,
            "message": _message(exc),
            "stack_trace": format_exception(exc),
            "stack_frames": parse_frames(
                exc,
                max_frames=config.max_frames,
                context_lines=config.context_lines,
                capture_locals=config.capture_locals,
                in_app_include=config.in_app_include,
            ),
        },
        "runtime": runtime_context(framework, framework_version),
    }

    if config.service:
        event["service"] = config.service
    if config.release:
        event["release"] = config.release
    if breadcrumbs:
        event["breadcrumbs"] = breadcrumbs
    if request:
        event["request"] = normalise_request(request)
    if user_context:
        event["user_context"] = user_context

    merged_tags = {**dict(config.tags), **(tags or {})}
    if merged_tags:
        event["tags"] = {str(key): str(value) for key, value in merged_tags.items()}
    if extra:
        event["extra"] = extra

    return event


def _message(exc: BaseException) -> str:
    """`str(exc)` can itself raise — `__str__` is customer code.

    An exception class whose `__str__` fails would otherwise turn a captured
    error into a second, uncaught one inside the capture path.
    """
    try:
        return str(exc)
    except Exception:  # the message is not worth a crash
        return f"<unprintable {type(exc).__name__}>"


def normalise_request(request: dict[str, Any]) -> dict[str, Any]:
    """Drop non-allowlisted headers before the payload leaves the process.

    The server does this too (`03` §S1 step 6). Doing it here as well means an
    `Authorization` or `Cookie` header never crosses the network at all, which
    is the only version of the control that holds if the transport is
    intercepted or the request is logged by something in between.
    """
    cleaned = dict(request)
    headers = cleaned.get("headers")
    if isinstance(headers, dict):
        cleaned["headers"] = {
            name: value for name, value in headers.items() if name.lower() in HEADER_ALLOWLIST
        }
    return cleaned
