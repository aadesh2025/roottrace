"""Per-event validation for `POST /v1/events` (`03` §S1, step 5).

> Invalid events are rejected individually; the batch is **NOT** failed
> wholesale. Response reports per-index errors.

That is the whole design constraint. One malformed event must not discard
ninety-nine valid ones — a client that loses a batch because of a single bad
payload loses the errors it most needed to send, and cannot do anything about
it from inside a crash handler.

So nothing here raises on bad input. Validation returns two lists, and the
caller persists one and reports the other.

Field caps **truncate rather than reject** (`03` §S1). A 70 KB stack trace is
still a usable error report; dropping it because it exceeded a limit would
discard the diagnosis to enforce a storage budget.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

#: `05` §5 and `03` §S1.
MAX_BATCH_SIZE = 100

#: `03` §S1 field size caps.
MAX_MESSAGE_BYTES = 8 * 1024
MAX_STACK_TRACE_BYTES = 64 * 1024
MAX_BODY_SAMPLE_BYTES = 4 * 1024
TRUNCATION_MARKER = "\n[truncated]"

#: `04` §7 `environment_kind`. The database enum is the authority; an event
#: naming anything else would fail the insert, so it is rejected here where the
#: client can be told which index was wrong.
VALID_ENVIRONMENTS = frozenset({"production", "staging", "development", "test"})

VALID_LEVELS = frozenset({"error", "fatal", "warning"})

#: How far back an event may be dated. `03` §S1's example rejects a timestamp
#: "more than 7 days in the past".
MAX_EVENT_AGE = timedelta(days=7)

#: And forward. Not in the spec, and deliberately added: a clock-skewed client
#: sending events dated next year would land in a partition that does not exist
#: yet and would sort above everything real in the dashboard forever. One hour
#: absorbs ordinary skew.
MAX_EVENT_SKEW = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class EventRejection:
    """One event that could not be accepted, reported by its index.

    The index is the client's — its position in the submitted batch — so a
    client can correlate the rejection with the event it holds without us
    echoing the payload back.
    """

    index: int
    code: str
    message: str

    def as_error(self) -> dict[str, Any]:
        return {"index": self.index, "code": self.code, "message": self.message}


@dataclass(frozen=True, slots=True)
class ValidatedEvent:
    """An accepted event, normalised into the shape `raw_events` stores."""

    index: int
    event_ts: datetime
    environment: str
    payload: dict[str, Any]
    payload_bytes: int
    service: str | None = None
    release: str | None = None
    event_id: str | None = None
    truncations: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class BatchValidation:
    accepted: tuple[ValidatedEvent, ...]
    rejected: tuple[EventRejection, ...]

    @property
    def all_invalid(self) -> bool:
        """`RT-INGEST-0010` — every event in the batch was rejected."""
        return bool(self.rejected) and not self.accepted


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _truncate(value: str, limit: int) -> tuple[str, bool]:
    encoded = value.encode()
    if len(encoded) <= limit:
        return value, False
    marker = TRUNCATION_MARKER.encode()
    kept = encoded[: max(0, limit - len(marker))]
    # Cut back to a character boundary: slicing UTF-8 by bytes can land inside
    # a multi-byte sequence, and a payload that will not decode is worse than
    # one that lost a few characters.
    return kept.decode(errors="ignore") + TRUNCATION_MARKER, True


def validate_event(
    index: int, event: Any, *, now: datetime | None = None
) -> tuple[ValidatedEvent | None, EventRejection | None]:
    """Validate one event. Returns exactly one of (accepted, rejection)."""
    now = now or datetime.now(UTC)

    def reject(code: str, message: str) -> tuple[None, EventRejection]:
        return None, EventRejection(index=index, code=code, message=message)

    if not isinstance(event, dict):
        return reject("RT-INGEST-0011", "event must be an object")

    error = event.get("error")
    if not isinstance(error, dict):
        return reject("RT-INGEST-0011", "error is required")
    if not error.get("type"):
        return reject("RT-INGEST-0011", "error.type is required")

    raw_timestamp = event.get("timestamp")
    if raw_timestamp is None:
        return reject("RT-INGEST-0011", "timestamp is required")
    event_ts = _parse_timestamp(raw_timestamp)
    if event_ts is None:
        return reject("RT-INGEST-0011", "timestamp is not a valid ISO-8601 instant")

    if event_ts < now - MAX_EVENT_AGE:
        return reject("RT-INGEST-0012", "timestamp is more than 7 days in the past")
    if event_ts > now + MAX_EVENT_SKEW:
        return reject("RT-INGEST-0012", "timestamp is more than 1 hour in the future")

    environment = event.get("environment")
    if environment is None:
        return reject("RT-INGEST-0011", "environment is required")
    if environment not in VALID_ENVIRONMENTS:
        return reject(
            "RT-INGEST-0013",
            f"unknown environment {environment!r}; expected one of "
            + ", ".join(sorted(VALID_ENVIRONMENTS)),
        )

    level = event.get("level", "error")
    if level not in VALID_LEVELS:
        return reject(
            "RT-INGEST-0011",
            f"unknown level {level!r}; expected one of " + ", ".join(sorted(VALID_LEVELS)),
        )

    frames = error.get("stack_frames")
    if frames is not None and not _frames_are_well_formed(frames):
        # RT-INGEST-0014. Rejected rather than dropped: a malformed frame list
        # would silently give retrieval nothing to resolve, and the case would
        # look like a retrieval failure rather than a bad payload.
        return reject("RT-INGEST-0014", "error.stack_frames is malformed")

    stack_trace = error.get("stack_trace")
    if stack_trace is not None and not isinstance(stack_trace, str):
        return reject("RT-INGEST-0014", "error.stack_trace must be a string")

    normalised, truncations = _apply_caps(event)
    encoded = json.dumps(normalised, separators=(",", ":"), sort_keys=True).encode()

    return (
        ValidatedEvent(
            index=index,
            event_ts=event_ts,
            environment=str(environment),
            payload=normalised,
            payload_bytes=len(encoded),
            service=_optional_str(event.get("service")),
            release=_optional_str(event.get("release")),
            event_id=_optional_str(event.get("event_id")),
            truncations=truncations,
        ),
        None,
    )


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _frames_are_well_formed(frames: Any) -> bool:
    if not isinstance(frames, list):
        return False
    for frame in frames:
        if not isinstance(frame, dict):
            return False
        if "file" in frame and not isinstance(frame["file"], str):
            return False
        if "line" in frame and not isinstance(frame["line"], int):
            return False
    return True


def _apply_caps(event: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Truncate over-long fields, recording which were cut.

    A copy is returned rather than mutating the caller's dictionary: the
    original body is written to object storage unmodified, and truncating it
    in place would destroy the thing the storage copy exists to preserve.
    """
    normalised = json.loads(json.dumps(event))
    truncations: list[str] = []

    error = normalised.get("error")
    if isinstance(error, dict):
        for key, limit in (
            ("message", MAX_MESSAGE_BYTES),
            ("stack_trace", MAX_STACK_TRACE_BYTES),
        ):
            value = error.get(key)
            if isinstance(value, str):
                error[key], was_cut = _truncate(value, limit)
                if was_cut:
                    truncations.append(f"error.{key}")

    request = normalised.get("request")
    if isinstance(request, dict):
        body = request.get("body_sample")
        if isinstance(body, str):
            request["body_sample"], was_cut = _truncate(body, MAX_BODY_SAMPLE_BYTES)
            if was_cut:
                truncations.append("request.body_sample")

    return normalised, tuple(truncations)


def validate_batch(events: list[Any], *, now: datetime | None = None) -> BatchValidation:
    """Validate a whole batch, keeping the valid events.

    The batch-size guard (`RT-INGEST-0003`) is the caller's: it rejects the
    request outright rather than producing per-index errors, because a batch
    of 500 is a client bug, not 500 bad events.
    """
    now = now or datetime.now(UTC)
    accepted: list[ValidatedEvent] = []
    rejected: list[EventRejection] = []

    for index, event in enumerate(events):
        valid, rejection = validate_event(index, event, now=now)
        if valid is not None:
            accepted.append(valid)
        elif rejection is not None:
            rejected.append(rejection)

    return BatchValidation(accepted=tuple(accepted), rejected=tuple(rejected))
