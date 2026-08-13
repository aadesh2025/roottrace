"""Ingest-time sanitisation (`03` §S1, `11` §8.2).

Runs before anything is persisted. Customer error payloads are hostile by
default: they carry whatever happened to be in scope when the process died,
which routinely includes credentials, card numbers and email addresses nobody
intended to send.

Two rules shape everything here.

**Redactions record `{path, kind}` and never the value.** The UI must be able
to show *that* something was redacted without storing *what* — a redaction log
containing the secret is a second copy of the secret with a reassuring name.

**Different from log redaction** (`11` §8.3, `roottrace_api/log.py`), and
deliberately so. That one runs over our own log lines, where a false positive
destroys the evidence an operator is reading; it matches credential formats
only. This one runs over customer payloads, where a false negative persists a
secret forever, so it also carries entropy, email and Luhn detection. Same
project, opposite tolerance for error.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Headers we keep. Everything else is dropped rather than redacted: an
#: allowlist cannot be defeated by a header we failed to think of, and a
#: denylist can. `Authorization` and `Cookie` are never stored under any
#: circumstances (`11` §8.2).
HEADER_ALLOWLIST = frozenset(
    {
        "accept",
        "accept-encoding",
        "content-encoding",
        "content-length",
        "content-type",
        "user-agent",
        "x-request-id",
    }
)

#: Shannon entropy above this, on a token longer than this, is treated as a
#: credential. `03` §S1 fixes both numbers.
ENTROPY_THRESHOLD = 4.5
ENTROPY_MIN_LENGTH = 20


@dataclass(frozen=True, slots=True)
class Redaction:
    path: str
    kind: str

    def as_record(self) -> dict[str, str]:
        # No `value` field, and there never will be one.
        return {"path": self.path, "kind": self.kind}


def _marker(kind: str) -> str:
    return f"[REDACTED:{kind}]"


#: Ordered. The connection-string rule runs first because the rest would
#: otherwise match inside a DSN and report the wrong kind.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("dsn_credentials", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")),
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}")),
    ("aws_key", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("provider_key", re.compile(r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("roottrace_key", re.compile(r"rt_(?:live|test)_[0-9a-f]{32}")),
    ("supabase_key", re.compile(r"sb_(?:secret|publishable)_[A-Za-z0-9_-]{12,}")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
)

#: Card-shaped runs, confirmed by Luhn before being redacted. The check matters:
#: a 16-digit order number is not a card, and redacting one would remove the
#: identifier an engineer needs to find the failing request.
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {character: value.count(character) for character in set(value)}
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for index, character in enumerate(digits):
        digit = int(character)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _redact_cards(value: str) -> tuple[str, bool]:
    found = False

    def replace(match: re.Match[str]) -> str:
        nonlocal found
        digits = re.sub(r"[ -]", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            found = True
            return _marker("pan")
        return match.group()

    return _CARD_CANDIDATE.sub(replace, value), found


#: Structured data dumped into a variable — a CSV document, a serialised row,
#: a joined list. High entropy, no whitespace once `repr` has escaped the
#: newlines, and not a credential. Found by running this over our own corpus,
#: where `resource-01` carries a 200-character CSV excerpt in a local variable.
_STRUCTURED = re.compile(r",|\\n|\\t")


def _looks_like_a_token(value: str) -> bool:
    """A secret is a token, not a paragraph and not a data dump.

    `03` §S1 says entropy applies "in a value position". Two restrictions make
    that usable:

    - **whitespace-free**, because a stack trace or a log line can sit near the
      threshold across its whole length, and redacting one would destroy the
      diagnosis to catch nothing;
    - **not delimited**, because `repr` escapes newlines, so a CSV excerpt in a
      local variable looks like one long high-entropy token.

    Both are deliberate false-negative trades: a credential containing a comma
    or a tab would slip past the entropy rule. The named patterns above do not
    depend on this, and they are what catch every credential format we know.
    """
    return (
        len(value) >= ENTROPY_MIN_LENGTH
        and not re.search(r"\s", value)
        and not _STRUCTURED.search(value)
    )


def sanitise_string(value: str, path: str) -> tuple[str, list[Redaction]]:
    """Redact one string, reporting what was found and where."""
    redactions: list[Redaction] = []

    for kind, pattern in _PATTERNS:
        if pattern.search(value):
            value = pattern.sub(_marker(kind), value)
            redactions.append(Redaction(path=path, kind=kind))

    value, had_card = _redact_cards(value)
    if had_card:
        redactions.append(Redaction(path=path, kind="pan"))

    # A value a named pattern already handled is not re-examined. Without
    # this, `postgresql://[REDACTED:dsn_credentials]@host:5432/db` is itself a
    # long high-entropy token and gets redacted a second time, reporting a kind
    # that says nothing about what was actually found.
    already_handled = "[REDACTED:" in value

    if (
        not already_handled
        and _looks_like_a_token(value)
        and shannon_entropy(value) > ENTROPY_THRESHOLD
    ):
        value = _marker("high_entropy")
        redactions.append(Redaction(path=path, kind="high_entropy"))

    return value, redactions


def _sanitise_headers(
    headers: Mapping[str, Any], path: str
) -> tuple[dict[str, Any], list[Redaction]]:
    kept: dict[str, Any] = {}
    redactions: list[Redaction] = []

    for name, value in headers.items():
        if name.lower() not in HEADER_ALLOWLIST:
            # Dropped, not redacted. A key that is not stored cannot leak.
            redactions.append(Redaction(path=f"{path}.{name}", kind="header_not_allowlisted"))
            continue
        cleaned, found = sanitise_string(str(value), f"{path}.{name}")
        kept[name] = cleaned
        redactions.extend(found)

    return kept, redactions


def _walk(value: Any, path: str) -> tuple[Any, list[Redaction]]:
    if isinstance(value, str):
        return sanitise_string(value, path)

    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        redactions: list[Redaction] = []
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if str(key).lower() == "headers" and isinstance(item, Mapping):
                cleaned[key], found = _sanitise_headers(item, child)
            else:
                cleaned[key], found = _walk(item, child)
            redactions.extend(found)
        return cleaned, redactions

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items: list[Any] = []
        redactions = []
        for index, item in enumerate(value):
            child, found = _walk(item, f"{path}[{index}]")
            items.append(child)
            redactions.extend(found)
        return items, redactions

    return value, []


def sanitise(payload: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Sanitise a whole event.

    Returns the cleaned payload and the redaction records for
    `raw_events.redactions`. The input is never mutated: the caller may still
    need the original, and a sanitiser that edits its argument makes that
    impossible to arrange later.
    """
    cleaned, redactions = _walk(dict(payload), "")
    return cleaned, [item.as_record() for item in redactions]
