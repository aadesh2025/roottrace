"""UUIDv7 for the SDK (`05` §1: UUIDv7 everywhere, `evt_`-prefixed on the wire).

**This is a deliberate copy of `apps/api/roottrace_api/ids.py`.** The SDK is
installed into customer applications, so `pyproject.toml` pins its dependency
set to empty — it cannot import the API package, and adding a shared runtime
package would give every customer of the SDK a transitive dependency on our
server code. Twenty lines duplicated is the cheaper of the two.

`tests/integration/test_sdk_uuid7_agreement.py` asserts the two implementations
produce identical layouts, so the copy cannot drift into a different id scheme
than the one the database and the API mint.
"""

from __future__ import annotations

import secrets
import time
import uuid

EVENT_ID_PREFIX = "evt_"

_UNIX_TS_MS_BITS = 48
_RAND_BITS = 74


def uuid7(*, now_ms: int | None = None) -> uuid.UUID:
    """A UUIDv7: 48 bits of Unix milliseconds, then 74 bits of randomness."""
    timestamp_ms = int(time.time() * 1000) if now_ms is None else now_ms
    if not 0 <= timestamp_ms < (1 << _UNIX_TS_MS_BITS):
        raise ValueError(f"timestamp outside the UUIDv7 range: {timestamp_ms}")

    value = timestamp_ms << 80
    value |= secrets.randbits(_RAND_BITS)
    value &= ~(0xF << 76)
    value |= 0x7 << 76
    value &= ~(0x3 << 62)
    value |= 0x2 << 62
    return uuid.UUID(int=value)


def new_event_id() -> str:
    """`evt_` followed by a UUIDv7 as 32 hex characters.

    Client-generated, and optional per `03` §S1 — the server mints one if it is
    absent. Sending it anyway is what lets a client correlate a buffered event
    with the batch that eventually carried it.
    """
    return f"{EVENT_ID_PREFIX}{uuid7().hex}"
