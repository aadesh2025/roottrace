"""The ±40-line / whole-file-under-400-lines extraction rule shared by
strategies A and B (`03` §S5, T4.3)."""

from __future__ import annotations

import pytest

from roottrace_worker.pipeline.retrieve.windowing import (
    CONTEXT_MARGIN,
    WHOLE_FILE_LINE_THRESHOLD,
    extract_window,
)

pytestmark = pytest.mark.unit


def _file(n_lines: int) -> str:
    return "\n".join(f"line {i}" for i in range(1, n_lines + 1))


def test_a_file_under_400_lines_is_taken_whole_even_with_a_center() -> None:
    """`03` §S5: if the file is under 400 lines, take the whole file."""
    content = _file(200)
    window, line_range, truncated = extract_window(content, center_range=(100, 105))
    assert window == content
    assert line_range == (1, 200)
    assert truncated is False


def test_a_file_over_400_lines_is_windowed_around_the_center() -> None:
    content = _file(1000)
    window, line_range, truncated = extract_window(content, center_range=(500, 510))
    assert line_range == (500 - CONTEXT_MARGIN, 510 + CONTEXT_MARGIN)
    assert truncated is True
    assert window.splitlines()[0] == f"line {line_range[0]}"
    assert window.splitlines()[-1] == f"line {line_range[1]}"


def test_the_margin_clips_at_the_start_of_the_file() -> None:
    content = _file(1000)
    _, line_range, truncated = extract_window(content, center_range=(10, 15))
    assert line_range[0] == 1
    assert truncated is True


def test_the_margin_clips_at_the_end_of_the_file() -> None:
    content = _file(1000)
    _, line_range, truncated = extract_window(content, center_range=(990, 995))
    assert line_range[1] == 1000
    assert truncated is True


def test_no_center_range_takes_the_whole_file_regardless_of_size() -> None:
    """No enclosing function was found (e.g. a module-level statement, or a
    frame with no line number) — the fallback is the whole file, not a
    guess at a window."""
    content = _file(1000)
    window, line_range, truncated = extract_window(content, center_range=None)
    assert window == content
    assert line_range == (1, 1000)
    assert truncated is False


def test_a_margin_wide_enough_to_swallow_the_whole_file_is_not_truncated() -> None:
    """A large file whose center is near the middle, with a margin covering
    the whole thing, should report the whole file honestly rather than a
    'window' that happens to equal it."""
    content = _file(WHOLE_FILE_LINE_THRESHOLD + 1)
    window, line_range, truncated = extract_window(
        content, center_range=(200, 210), margin=WHOLE_FILE_LINE_THRESHOLD
    )
    assert window == content
    assert line_range == (1, WHOLE_FILE_LINE_THRESHOLD + 1)
    assert truncated is False


def test_an_empty_file_does_not_raise() -> None:
    _, line_range, truncated = extract_window("", center_range=(1, 1))
    assert line_range == (1, 1)
    assert truncated is False


def test_line_range_is_always_one_based_and_inclusive() -> None:
    content = _file(1000)
    _, (start, end), _ = extract_window(content, center_range=(500, 500))
    assert content.splitlines()[start - 1] == f"line {start}"
    assert content.splitlines()[end - 1] == f"line {end}"
