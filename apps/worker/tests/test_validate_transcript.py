"""`transcript.py` (T6.2, `07` §7) — sanitisation and middle-truncation of
captured sandbox output. No container needed."""

from __future__ import annotations

import pytest

from roottrace_worker.pipeline.validate.transcript import sanitize, truncate_middle

pytestmark = pytest.mark.unit


def test_ansi_escapes_are_stripped() -> None:
    assert sanitize("\x1b[31mred\x1b[0m text") == "red text"


def test_control_characters_are_stripped() -> None:
    assert sanitize("hello\x00\x07world") == "helloworld"


def test_newlines_carriage_returns_and_tabs_survive() -> None:
    text = "line one\nline\ttwo\r\n"
    assert sanitize(text) == text


def test_text_within_the_cap_is_not_truncated() -> None:
    result = truncate_middle("short text", max_bytes=1000)
    assert result.text == "short text"
    assert not result.truncated
    assert result.original_bytes == len(b"short text")


def test_text_exceeding_the_cap_keeps_head_and_tail() -> None:
    text = ("A" * 1000) + ("B" * 1000) + ("C" * 1000)
    result = truncate_middle(text, max_bytes=600)

    assert result.truncated
    assert result.text.startswith("A" * 100)
    assert result.text.endswith("C" * 100)
    assert "B" * 1000 not in result.text
    assert "truncated" in result.text
    assert result.original_bytes == 3000


def test_truncation_marker_reports_the_dropped_byte_count() -> None:
    text = "X" * 10_000
    result = truncate_middle(text, max_bytes=100)
    assert "truncated 9900 bytes" in result.text
