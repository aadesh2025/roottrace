"""Structured logging and the redaction processor.

`12` §2 defines the field set; `11` §8.3 defines redaction. Redaction is a
**processor in the chain**, not a convention at the call site, so a developer
writing ``logger.info("config", **settings.model_dump())`` cannot bypass it —
that exact line is the failure mode the doc names.

Standard library logging is routed through the same chain, so a log line from
uvicorn, httpx or any other dependency is rendered and redacted identically. A
redaction filter that only covers our own logger is the kind of control that
looks applied and is not: the credential in a connection string usually escapes
through somebody else's library, not ours.

This lives in `api` because `api` is the only service that exists. When the
worker needs it (T2.x) it moves to a shared package rather than being copied —
two divergent redaction chains would be worse than none, because both would
look correct.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
import sys
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import Any, TextIO

import structlog

from roottrace_api.settings import Settings

# ── Redaction ──────────────────────────────────────────────────────────────

#: Key names whose value is never loggable, whatever it contains (`11` §8.3).
REDACT_KEYS = re.compile(
    r"(api[_-]?key|token|secret|password|authorization|cookie|private[_-]?key|dsn"
    r"|access[_-]?key|refresh[_-]?token|client[_-]?secret)",
    re.I,
)

REDACTED = "[REDACTED]"

#: Value patterns, for credentials that arrive inside an innocuously named field
#: — an exception message, a URL, a stack frame.
#:
#: Deliberately narrower than the ingest-time sanitiser (`03` §S1, T2.2). That
#: one runs over customer payloads and can afford Shannon entropy, email and
#: Luhn detection. This one runs over our own log lines, where a false positive
#: silently destroys the evidence an operator is reading at 03:00. Every pattern
#: here matches a credential *format*, not a credential-shaped string.
_VALUE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Credentials embedded in a connection string: postgres://user:pw@host.
    # First, because the rest would otherwise match inside it.
    (re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)"), REDACTED),
    # Our own ingest keys (`05` §2.1).
    (re.compile(r"rt_(?:live|test)_[0-9a-f]{32}"), REDACTED),
    # JWTs — three base64url segments. Access and refresh tokens both.
    (re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}"), REDACTED),
    # Supabase's newer key format.
    (re.compile(r"sb_(?:secret|publishable)_[A-Za-z0-9_-]{12,}"), REDACTED),
    # GitHub: ghp_ gho_ ghu_ ghs_ ghr_ and fine-grained github_pat_.
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), REDACTED),
    # AWS access key id.
    (re.compile(r"AKIA[0-9A-Z]{16}"), REDACTED),
    # OpenAI / Anthropic style.
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), REDACTED),
    # Slack.
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), REDACTED),
    # PEM private keys: redact the whole body, not just the header.
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
        REDACTED,
    ),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), REDACTED),
    # A bearer credential quoted in prose or an exception message.
    (re.compile(r"(?i)(?<=bearer )[A-Za-z0-9._~+/=-]{10,}"), REDACTED),
)


def redact_patterns(value: str) -> str:
    """Replace credential formats appearing anywhere inside a string."""
    for pattern, replacement in _VALUE_PATTERNS:
        value = pattern.sub(replacement, value)
    return value


def redact(value: Any, *, key: str | None = None) -> Any:
    """Recursively redact a log record.

    `11` §8.3's reference implementation walks dicts and strings only. A secret
    inside a list — ``{"keys": [{"token": "..."}]}``, or the list of headers on
    a request — passed through it untouched, which is the same shape of defect
    as a hook that exits 0 without scanning: applied, and doing nothing. Lists,
    tuples and sets are walked here, and the doc is corrected to match.
    """
    if key is not None and REDACT_KEYS.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_patterns(value)
    if isinstance(value, Mapping):
        return {k: redact(v, key=str(k)) for k, v in value.items()}
    # `str` and `bytes` are Sequences and must not reach this branch; both are
    # handled above or fall through to the return below.
    if isinstance(value, (list, tuple, set, frozenset)) or (
        isinstance(value, Sequence) and not isinstance(value, (str, bytes))
    ):
        items = list(value)
        # A (name, value) pair — the shape ASGI carries headers in — is treated
        # as a key and its value. Without this, only the value patterns protect
        # a header list, and they cover `Bearer <token>` but not an opaque
        # credential under a scheme they do not know.
        if len(items) == 2 and isinstance(items[0], str) and REDACT_KEYS.search(items[0]):
            return [items[0], REDACTED]
        return [redact(item) for item in items]
    return value


def _redaction_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """The chain's last stop before rendering (`12` §2.3).

    Last on purpose: it runs after `format_exc_info` has turned an exception
    into text, so a credential inside a traceback is covered too. An exception
    is a very ordinary way for a connection string to reach a log.
    """
    return {k: redact(v, key=str(k)) for k, v in event_dict.items()}


# ── Field shaping ──────────────────────────────────────────────────────────


def _timestamp(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """ISO-8601 UTC with milliseconds, matching `12` §2.1 exactly.

    structlog's own TimeStamper emits microseconds; the contract shows three
    decimal places, and log aggregators do parse this field.
    """
    now = dt.datetime.now(dt.UTC)
    event_dict["timestamp"] = f"{now.strftime('%Y-%m-%dT%H:%M:%S')}.{now.microsecond // 1000:03d}Z"
    return event_dict


def _rename_event_to_message(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog calls it `event`; `12` §2.1 calls it `message`."""
    if "event" in event_dict:
        event_dict["message"] = event_dict.pop("event")
    return event_dict


def _static_fields(
    settings: Settings,
) -> Callable[[Any, str, MutableMapping[str, Any]], MutableMapping[str, Any]]:
    """`service`, `version` and `environment` on every line (`12` §2.1)."""

    def processor(
        _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
    ) -> MutableMapping[str, Any]:
        event_dict.setdefault("service", settings.service_name)
        event_dict.setdefault("version", settings.version)
        event_dict.setdefault("environment", settings.environment)
        return event_dict

    return processor


# ── Configuration ──────────────────────────────────────────────────────────


def configure_logging(settings: Settings, *, stream: TextIO | None = None) -> None:
    """Install the processor chain, for structlog and for stdlib logging alike.

    Idempotent: calling it twice replaces the configuration rather than stacking
    handlers, so a test that builds several apps does not get duplicated lines.
    """
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _static_fields(settings),
        _timestamp,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _rename_event_to_message,
        # Redaction is last. Anything added after it would be unredacted.
        _redaction_processor,
    ]

    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # Everything that uses stdlib logging — uvicorn, httpx, our own libraries —
    # is rendered by the same chain. `foreign_pre_chain` is what applies the
    # chain to records that never went through structlog.
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(),
            ],
        )
    )

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # uvicorn installs its own handlers at import time and would otherwise emit
    # a second, unredacted, unstructured copy of every line.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    # `uvicorn.access` is silenced here rather than by uvicorn's
    # `--no-access-log`, because this function runs from `create_app()` — after
    # uvicorn has configured logging — and the loop above re-enabled
    # propagation on a logger uvicorn had just switched off. The flag looked
    # applied and produced a duplicate access line for every request.
    #
    # Our own `http_request` line replaces it and carries what uvicorn's does
    # not: the request id and the duration.
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
