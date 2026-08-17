"""The extraction window shared by strategies A and B (`03` §S5).

> Extract the enclosing function ±40 lines. If the file is < 400 lines, take
> the whole file — surrounding code is usually worth more than the tokens it
> costs.

One rule, one place. Strategy A applies it around a stack frame's enclosing
function; strategy B applies it around a resolved callee, caller, or type
definition — both want the identical trade-off between "just the function"
and "the whole file", so it is not reimplemented per strategy.
"""

from __future__ import annotations

#: `03` §S5, literal values.
CONTEXT_MARGIN = 40
WHOLE_FILE_LINE_THRESHOLD = 400


def extract_window(
    content: str, *, center_range: tuple[int, int] | None, margin: int = CONTEXT_MARGIN
) -> tuple[str, tuple[int, int], bool]:
    """`(window_content, line_range, truncated)`.

    `line_range` is always 1-based and inclusive, matching every other line
    number in this codebase (`03` §S1's stack frames, `08`'s blame ranges).
    `truncated` is `False` exactly when `window_content` is the whole file —
    the signal `files[].truncated` in `03` §S5's output contract carries.
    """
    lines = content.splitlines()
    total = len(lines)
    if total == 0:
        return content, (1, 1), False

    if total <= WHOLE_FILE_LINE_THRESHOLD or center_range is None:
        return content, (1, total), False

    start = max(1, center_range[0] - margin)
    end = min(total, center_range[1] + margin)
    if start <= 1 and end >= total:
        # The margin swallowed the whole file anyway.
        return content, (1, total), False

    window = "\n".join(lines[start - 1 : end])
    return window, (start, end), True
