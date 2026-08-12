"""UUIDv7 layout and the `request_id` encoding (T1.5).

Python 3.12 has no `uuid.uuid7`, so this is our implementation of RFC 9562 and
not a thin wrapper over a stdlib call. It is tested accordingly.
"""

from __future__ import annotations

import uuid

import pytest

from roottrace_api.ids import REQUEST_ID_PREFIX, new_request_id, uuid7

pytestmark = pytest.mark.unit


def test_version_and_variant_bits_are_set() -> None:
    """Version 7 and the RFC 4122 variant sit inside the random span, so both
    are set after the randomness is filled in. Getting that order wrong yields
    a value that is random, unique, and not a UUIDv7 — which nothing would
    notice until something sorted by it."""
    for _ in range(200):
        value = uuid7()
        assert value.version == 7
        assert value.variant == uuid.RFC_4122


def test_the_timestamp_is_recoverable() -> None:
    """The first 48 bits are Unix milliseconds. That is the whole reason for
    choosing v7 over v4 — an id that sorts by creation time."""
    minted = uuid7(now_ms=1_754_301_600_123)
    assert minted.int >> 80 == 1_754_301_600_123


def test_ids_sort_by_creation_time() -> None:
    """Ordering across milliseconds, asserted with an injected clock rather
    than a sleep — a test that sleeps to cross a millisecond boundary is the
    flaky kind."""
    ids = [str(uuid7(now_ms=base)) for base in range(1_754_301_600_000, 1_754_301_600_050)]
    assert ids == sorted(ids)


def test_ids_are_unique_within_one_millisecond() -> None:
    """74 bits of randomness per millisecond. Ordering within a millisecond is
    explicitly not promised; uniqueness is."""
    ids = {uuid7(now_ms=1_754_301_600_000) for _ in range(5_000)}
    assert len(ids) == 5_000


@pytest.mark.parametrize("out_of_range", [-1, 1 << 48])
def test_a_timestamp_outside_48_bits_is_refused(out_of_range: int) -> None:
    """Silently truncating would corrupt the version bits and produce a
    plausible-looking id."""
    with pytest.raises(ValueError, match="outside the UUIDv7 range"):
        uuid7(now_ms=out_of_range)


def test_request_id_is_a_lossless_encoding() -> None:
    """`req_` + 32 hex characters, with the UUID recoverable from it. A
    `request_id` that cannot be parsed back is just a random string, and the
    UUIDv7 requirement in `05` §1 would be decorative."""
    request_id = new_request_id()
    assert request_id.startswith(REQUEST_ID_PREFIX)

    body = request_id.removeprefix(REQUEST_ID_PREFIX)
    assert len(body) == 32
    assert "-" not in body
    assert uuid.UUID(body).version == 7


def test_request_ids_do_not_repeat() -> None:
    assert len({new_request_id() for _ in range(5_000)}) == 5_000
