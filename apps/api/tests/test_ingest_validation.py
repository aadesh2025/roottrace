"""Per-event validation with partial success (T2.1, `03` §S1 step 5).

T2.1's acceptance: *two invalid events are rejected individually while 98
succeed.* That is asserted directly below, and the corpus built in T3.2 is used
as the known-good input — which is the reason fixtures were built before
ingestion. Validating against hand-written payloads would prove the validator
accepts what I imagined, not what the SDK produces.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from roottrace_api.ingest.events import (
    MAX_BATCH_SIZE,
    MAX_MESSAGE_BYTES,
    MAX_STACK_TRACE_BYTES,
    TRUNCATION_MARKER,
    validate_batch,
    validate_event,
)

pytestmark = pytest.mark.unit

CORPUS = Path(__file__).resolve().parents[3] / "fixtures" / "error-corpus"

#: The corpus is dated 2026-08-04; the age window is 7 days, so validation is
#: pinned to a clock just after it rather than run against `now`. Without this
#: every fixture would start failing `RT-INGEST-0012` a week after it was
#: generated — a test that rots into a failure on a calendar boundary.
CORPUS_CLOCK = datetime(2026, 8, 4, 10, 0, tzinfo=UTC)

CASE_IDS = sorted(
    path.name.removesuffix(".json") for path in CORPUS.glob("*.json") if ".case." not in path.name
)


def corpus_event(case_id: str) -> dict[str, Any]:
    body = json.loads((CORPUS / f"{case_id}.json").read_text(encoding="utf-8"))
    return dict(body["events"][0])


def valid_event(**overrides: Any) -> dict[str, Any]:
    event = corpus_event("null-prop-01")
    event.update(overrides)
    return event


# ── The corpus is accepted ─────────────────────────────────────────────────


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_corpus_payload_is_accepted(case_id: str) -> None:
    """The 25 real payloads must pass. If the validator rejects one, either the
    validator or the corpus is wrong, and both are ours to fix."""
    result = validate_batch([corpus_event(case_id)], now=CORPUS_CLOCK)

    assert result.rejected == (), result.rejected
    assert len(result.accepted) == 1


def test_there_are_25_corpus_payloads() -> None:
    assert len(CASE_IDS) == 25


# ── Partial success — the acceptance criterion ─────────────────────────────


def test_two_invalid_events_are_rejected_while_98_succeed() -> None:
    """T2.1 verbatim.

    One malformed event must not discard ninety-nine valid ones: a client that
    loses a batch because of a single bad payload loses the errors it most
    needed to send, from inside a crash handler where it can do nothing about
    it.
    """
    events: list[Any] = [valid_event() for _ in range(MAX_BATCH_SIZE)]
    events[14] = valid_event(error={"message": "no type here"})
    events[71] = valid_event(timestamp="2020-01-01T00:00:00Z")

    result = validate_batch(events, now=CORPUS_CLOCK)

    assert len(result.accepted) == 98
    assert [rejection.index for rejection in result.rejected] == [14, 71]
    assert result.rejected[0].code == "RT-INGEST-0011"
    assert result.rejected[1].code == "RT-INGEST-0012"


def test_rejections_report_the_clients_own_index() -> None:
    """So a client can correlate the failure with the event it still holds,
    without us echoing an untrusted payload back to it."""
    events: list[Any] = [valid_event(), "not an object", valid_event()]
    result = validate_batch(events, now=CORPUS_CLOCK)

    assert [item.index for item in result.accepted] == [0, 2]
    assert result.rejected[0].index == 1


def test_a_wholly_invalid_batch_is_detectable() -> None:
    """`RT-INGEST-0010` is a different response from partial success, so the
    caller needs to be able to tell them apart."""
    result = validate_batch([{}, {}], now=CORPUS_CLOCK)

    assert result.all_invalid
    assert not validate_batch([valid_event(), {}], now=CORPUS_CLOCK).all_invalid


def test_an_empty_batch_is_not_wholly_invalid() -> None:
    assert not validate_batch([], now=CORPUS_CLOCK).all_invalid


# ── Registered codes, per rule ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"error": {}}, "RT-INGEST-0011"),
        ({"error": {"message": "m"}}, "RT-INGEST-0011"),
        ({"timestamp": None}, "RT-INGEST-0011"),
        ({"timestamp": "not-a-date"}, "RT-INGEST-0011"),
        ({"environment": None}, "RT-INGEST-0011"),
        ({"level": "critical"}, "RT-INGEST-0011"),
        ({"timestamp": "2019-01-01T00:00:00Z"}, "RT-INGEST-0012"),
        ({"environment": "prod"}, "RT-INGEST-0013"),
    ],
)
def test_each_rule_reports_its_registered_code(override: dict[str, Any], code: str) -> None:
    valid, rejection = validate_event(0, valid_event(**override), now=CORPUS_CLOCK)

    assert valid is None
    assert rejection is not None
    assert rejection.code == code


def test_a_future_timestamp_is_refused() -> None:
    """Not in `03` §S1 and added deliberately: a clock-skewed client sending
    events dated next year would land in a partition that does not exist yet
    and sort above everything real in the dashboard forever."""
    _, rejection = validate_event(
        0, valid_event(timestamp="2027-01-01T00:00:00Z"), now=CORPUS_CLOCK
    )
    assert rejection is not None
    assert rejection.code == "RT-INGEST-0012"


def test_ordinary_clock_skew_is_tolerated() -> None:
    """The other half. A validator that rejected a device three seconds fast
    would drop real errors for a reason nobody could diagnose."""
    slightly_ahead = (CORPUS_CLOCK + timedelta(minutes=5)).isoformat().replace("+00:00", "Z")
    valid, rejection = validate_event(0, valid_event(timestamp=slightly_ahead), now=CORPUS_CLOCK)

    assert rejection is None
    assert valid is not None


@pytest.mark.parametrize(
    "frames",
    ["not a list", [{"file": 12}], [{"line": "not an int"}], ["not a frame"]],
)
def test_a_malformed_frame_list_is_refused(frames: Any) -> None:
    """`RT-INGEST-0014`. Rejected rather than dropped: silently discarding the
    frames would leave retrieval with nothing to resolve, and the case would
    look like a retrieval failure rather than a bad payload."""
    event = valid_event()
    event["error"] = {**event["error"], "stack_frames": frames}

    _, rejection = validate_event(0, event, now=CORPUS_CLOCK)
    assert rejection is not None
    assert rejection.code == "RT-INGEST-0014"


def test_a_missing_frame_list_is_fine() -> None:
    """`03` §S1 marks `stack_frames` optional — the SDK pre-parses when it
    can, and a plain stack trace is still a usable report."""
    event = valid_event()
    event["error"] = {key: value for key, value in event["error"].items() if key != "stack_frames"}

    valid, rejection = validate_event(0, event, now=CORPUS_CLOCK)
    assert rejection is None
    assert valid is not None


# ── Caps truncate, they do not reject ──────────────────────────────────────


def test_an_oversized_stack_trace_is_truncated_not_rejected() -> None:
    """A 70 KB stack trace is still a usable error report. Dropping it to
    enforce a storage budget would discard the diagnosis."""
    event = valid_event()
    event["error"] = {**event["error"], "stack_trace": "x" * (MAX_STACK_TRACE_BYTES + 5_000)}

    valid, rejection = validate_event(0, event, now=CORPUS_CLOCK)

    assert rejection is None
    assert valid is not None
    assert valid.truncations == ("error.stack_trace",)
    assert len(valid.payload["error"]["stack_trace"].encode()) <= MAX_STACK_TRACE_BYTES
    assert valid.payload["error"]["stack_trace"].endswith(TRUNCATION_MARKER)


def test_an_oversized_message_is_truncated() -> None:
    event = valid_event()
    event["error"] = {**event["error"], "message": "y" * (MAX_MESSAGE_BYTES + 100)}

    valid, _ = validate_event(0, event, now=CORPUS_CLOCK)
    assert valid is not None
    assert valid.truncations == ("error.message",)


def test_truncation_never_produces_undecodable_text() -> None:
    """Slicing UTF-8 by bytes can land inside a multi-byte sequence. A payload
    that will not decode is worse than one that lost a few characters."""
    event = valid_event()
    event["error"] = {**event["error"], "message": "é" * MAX_MESSAGE_BYTES}

    valid, _ = validate_event(0, event, now=CORPUS_CLOCK)
    assert valid is not None
    json.dumps(valid.payload)  # round-trips


def test_truncation_does_not_mutate_the_callers_event() -> None:
    """The unmodified body is written to object storage; truncating in place
    would destroy the thing that copy exists to preserve."""
    event = valid_event()
    original = "z" * (MAX_MESSAGE_BYTES + 10)
    event["error"] = {**event["error"], "message": original}

    validate_event(0, event, now=CORPUS_CLOCK)
    assert event["error"]["message"] == original


# ── Normalisation for the raw_events row ───────────────────────────────────


def test_an_accepted_event_carries_what_the_row_needs() -> None:
    valid, _ = validate_event(0, valid_event(), now=CORPUS_CLOCK)

    assert valid is not None
    assert valid.environment == "production"
    assert valid.service == "checkout-api"
    assert valid.release == "v2.14.3"
    assert valid.event_ts.tzinfo is not None
    assert valid.payload_bytes > 0


def test_a_naive_timestamp_is_treated_as_utc() -> None:
    """Every timestamp is stored UTC (CLAUDE.md). A naive one from an older
    SDK must not be silently interpreted as the server's local time."""
    valid, _ = validate_event(0, valid_event(timestamp="2026-08-04T09:14:22.481"), now=CORPUS_CLOCK)
    assert valid is not None
    assert valid.event_ts.utcoffset() == timedelta(0)


def test_optional_strings_are_normalised_to_none() -> None:
    valid, _ = validate_event(0, valid_event(service="", release=None), now=CORPUS_CLOCK)

    assert valid is not None
    assert valid.service is None
    assert valid.release is None
