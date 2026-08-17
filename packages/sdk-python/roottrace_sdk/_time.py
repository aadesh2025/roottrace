"""Timestamp formatting.

`05` §1 fixes the wire format: ISO-8601 UTC **with milliseconds** —
`2026-08-04T09:14:22.481Z`. `datetime.isoformat()` produces microseconds and
`+00:00`, neither of which matches, and the difference is not cosmetic: the
fixture corpus and the ingest validator both read these literally.
"""

from __future__ import annotations

from datetime import UTC, datetime


def isoformat_ms(value: datetime) -> str:
    utc = value.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
