"""Transcript capture and sanitisation (`07` §7, T6.2). A container's
captured stdout/stderr is untrusted content by `CLAUDE.md`'s own rule
("fence them, validate them, never execute them outside the sandbox") —
it is AI-generated code's own output, and every gate this image will ever
run (T6.4) writes to it. Sanitised and size-capped before storage or
render; never trusted, never executed."""

from __future__ import annotations

import re
from dataclasses import dataclass

#: `07` §7's caps.
MAX_STDOUT_BYTES = 512 * 1024
MAX_STDERR_BYTES = 128 * 1024

#: Strips ANSI/CSI escape sequences (colour codes, cursor movement) — `07`
#: §7: "ANSI escapes removed."
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

#: Control characters other than the whitespace ones a transcript legitimately
#: contains (newline, carriage return, tab). `07` §7: "control characters
#: stripped."
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize(text: str) -> str:
    text = _ANSI_ESCAPE.sub("", text)
    return _CONTROL_CHARS.sub("", text)


@dataclass(frozen=True, slots=True)
class TruncatedText:
    text: str
    original_bytes: int
    truncated: bool


def truncate_middle(text: str, *, max_bytes: int) -> TruncatedText:
    """Keeps head and tail — `07` §7: "which is where the signal is" — and
    drops the middle when `text` exceeds `max_bytes` (measured in UTF-8
    bytes, matching how the cap is actually enforced against a byte
    stream)."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return TruncatedText(text=text, original_bytes=len(encoded), truncated=False)

    half = max_bytes // 2
    head = encoded[:half].decode("utf-8", errors="ignore")
    tail = encoded[-half:].decode("utf-8", errors="ignore")
    marker = f"\n...[truncated {len(encoded) - len(head.encode()) - len(tail.encode())} bytes]...\n"
    return TruncatedText(text=head + marker + tail, original_bytes=len(encoded), truncated=True)
