"""Fingerprinting (`03` §S2).

Collapses identical errors into one Issue, so a 10,000-occurrence storm becomes
one investigation rather than ten thousand.

> Fingerprinting deserves the most unit-test attention of anything in the
> system. Over-grouping merges distinct bugs into one issue and the AI
> investigates the wrong one; under-grouping creates a thousand issues for one
> bug and burns the cost budget. Both failures are expensive and both are
> silent. (`14` §3)

Two decisions carry that weight:

**Messages are normalised before hashing.** `"User 8821 not found"` and
`"User 9134 not found"` are one bug; hashing the raw message makes them two
thousand.

**Frames contribute file and function, never line numbers.** A line number
shifts with every unrelated commit above it, so including one re-fingerprints
the same bug after a formatting change — the same defect arriving as a new
issue every deploy.

Lives in `api` because S1 and S2 share the normalisation and the api is where
the code currently is; S2 itself runs in the worker (`03` §S2). When the worker
exists this moves with it rather than being copied.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections.abc import Mapping, Sequence
from typing import Any

#: `03` §S2. The separator is a unit separator so a value containing it cannot
#: forge a different grouping by splicing fields together.
FIELD_SEPARATOR = "\x1f"

FINGERPRINT_LENGTH = 32

TOP_FRAMES = 5

#: Applied **in order** — the table in `03` §S2. Order matters: UUIDs before
#: hex (a UUID is hex with hyphens), timestamps before integers (a timestamp is
#: full of them), URLs before paths.
_NORMALISERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "<uuid>",
    ),
    (
        re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
        "<ts>",
    ),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "<email>"),
    (re.compile(r"\bhttps?://[^\s\"']+"), "<url>"),
    (re.compile(r"0x[0-9a-fA-F]+"), "<addr>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "<ip>"),
    (re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}\b"), "<ip>"),
    # Absolute paths, POSIX and Windows.
    (re.compile(r"(?:[A-Za-z]:)?[\\/](?:[\w.\-]+[\\/]){1,}[\w.\-]+"), "<path>"),
    (re.compile(r"\b[0-9a-fA-F]{8,}\b"), "<hex>"),
    (re.compile(r"'[^']*'|\"[^\"]*\""), "<str>"),
    (re.compile(r"\b\d{3,}\b"), "<num>"),
)


def normalize_message(message: str | None) -> str:
    """Strip the variable data out of an error message.

    Idempotent by construction: every replacement is an angle-bracket token
    that no rule matches. `14` §3 asserts it, because a normaliser that keeps
    changing its own output would make a fingerprint depend on how many times
    it had been computed.
    """
    if not message:
        return ""

    normalised = message
    for pattern, replacement in _NORMALISERS:
        normalised = pattern.sub(replacement, normalised)
    return " ".join(normalised.split())


def top_in_app_frames(frames: Sequence[Mapping[str, Any]] | None, n: int = TOP_FRAMES) -> list[str]:
    """The deepest `n` in-app frames, as `basename::function`.

    Deliberately no line numbers. That is the decision that makes a fingerprint
    survive a refactor while still separating genuinely different code paths.

    Frames arrive innermost-first (`03` §S1), so "deepest" is simply the front
    of the list.
    """
    if not frames:
        return []

    reduced: list[str] = []
    for frame in frames:
        if not frame.get("in_app"):
            continue
        raw = str(frame.get("file", "")).replace("\\", "/")
        basename = posixpath.basename(raw) or raw
        reduced.append(f"{basename}::{frame.get('function', '')}")
        if len(reduced) == n:
            break
    return reduced


def _by_path(event: Mapping[str, Any], dotted: str) -> Any:
    cursor: Any = event
    for part in dotted.split("."):
        if not isinstance(cursor, Mapping):
            return None
        cursor = cursor.get(part)
    return cursor


def _custom_group_values(event: Mapping[str, Any], group_by: Sequence[str]) -> list[str]:
    values: list[str] = []
    for field in group_by:
        if field.startswith("frames["):
            index = int(field[len("frames[") : field.index("]")])
            attribute = field.rsplit(".", 1)[-1]
            frames = _by_path(event, "error.stack_frames") or []
            frame = frames[index] if index < len(frames) else {}
            values.append(str(frame.get(attribute, "")))
        elif field == "error.message":
            values.append(normalize_message(_by_path(event, field)))
        else:
            values.append("" if (value := _by_path(event, field)) is None else str(value))
    return values


def _matches(event: Mapping[str, Any], match: Mapping[str, Any]) -> bool:
    for field, expected in match.items():
        actual = _by_path(event, field)
        pattern = str(expected)
        if pattern.endswith("*"):
            if not str(actual or "").startswith(pattern[:-1]):
                return False
        elif str(actual) != pattern:
            return False
    return True


def fingerprint_input(
    event: Mapping[str, Any], rules: Sequence[Mapping[str, Any]] | None = None
) -> list[str]:
    """The exact list that gets hashed. Separated out so a test can read it.

    A custom rule replaces the default inputs entirely — that is what a project
    configuring `group_by` is asking for. The first matching rule wins, so
    ordering in the project's configuration is meaningful.
    """
    for rule in rules or ():
        if _matches(event, rule.get("match", {})):
            return _custom_group_values(event, rule.get("group_by", ()))

    error = event.get("error") or {}
    request = event.get("request") or {}
    return [
        str(error.get("type") or ""),
        normalize_message(error.get("message")),
        "|".join(top_in_app_frames(error.get("stack_frames"))),
        str(request.get("route_pattern") or ""),
    ]


def compute_fingerprint(
    event: Mapping[str, Any], rules: Sequence[Mapping[str, Any]] | None = None
) -> str:
    """`sha256` of the joined inputs, truncated to 32 hex characters."""
    joined = FIELD_SEPARATOR.join(fingerprint_input(event, rules))
    return hashlib.sha256(joined.encode()).hexdigest()[:FINGERPRINT_LENGTH]
