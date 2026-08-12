"""Identifier generation.

UUIDv7 everywhere (CLAUDE.md, `05` §1). Postgres mints its own via
`uuid_generate_v7()` (`04` §2); this is the application-side equivalent, needed
for identifiers that exist before any row does — `request_id` being the first.

Python 3.12 has no `uuid.uuid7` (it lands in 3.14), so the layout is
implemented here against RFC 9562 §5.2 rather than approximated with `uuid4`.
"""

from __future__ import annotations

import secrets
import time
import uuid

REQUEST_ID_PREFIX = "req_"

_UNIX_TS_MS_BITS = 48
_RAND_BITS = 74


def uuid7(*, now_ms: int | None = None) -> uuid.UUID:
    """A UUIDv7: 48 bits of Unix milliseconds, then 74 bits of randomness.

    `now_ms` is injectable so ordering can be asserted without sleeping. A test
    that sleeps to cross a millisecond boundary is the flaky kind the testing
    standard rules out.

    Ordering holds *between* milliseconds, not within one. Two ids minted in the
    same millisecond sort arbitrarily with respect to each other, and nothing
    here relies on them not doing so.
    """
    timestamp_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if not 0 <= timestamp_ms < (1 << _UNIX_TS_MS_BITS):
        raise ValueError(f"timestamp outside the UUIDv7 range: {timestamp_ms}")

    value = timestamp_ms << 80
    value |= secrets.randbits(_RAND_BITS)
    # Version 7 in bits 48-51, RFC 4122 variant (0b10) in bits 64-65. Both sit
    # inside the random span above, so they are cleared before being set.
    value &= ~(0xF << 76)
    value |= 0x7 << 76
    value &= ~(0x3 << 62)
    value |= 0x2 << 62
    return uuid.UUID(int=value)


def new_request_id() -> str:
    """`req_` followed by a UUIDv7 as 32 hex characters.

    Hyphens are dropped so the identifier is one token: a user pasting it out of
    a support ticket and an operator grepping it out of a log line both handle a
    single word. The encoding is lossless —
    ``uuid.UUID(value.removeprefix("req_"))`` recovers the id.

    `05` §1 fixes the rule (UUIDv7, prefixed); its `req_01J2K3M4N5` examples are
    abbreviated for readability, not literal.
    """
    return f"{REQUEST_ID_PREFIX}{uuid7().hex}"
