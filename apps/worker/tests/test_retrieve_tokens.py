"""Token estimation for the 24,000-token hard budget (`03` §S5, P3, T4.4).

No tokenizer dependency — see `tokens.py`'s module docstring for the reasoning
(no single library is exact for the two-provider routing `06` §2.2 uses, so a
deliberately conservative estimate is the safer choice than a false
precision).
"""

from __future__ import annotations

import pytest

from roottrace_worker.pipeline.retrieve.tokens import CHARS_PER_TOKEN, estimate_tokens

pytestmark = pytest.mark.unit


def test_empty_text_is_zero_tokens() -> None:
    assert estimate_tokens("") == 0


def test_a_single_character_is_never_zero() -> None:
    """An empty estimate for non-empty text would make a one-character
    fragment look free to the budget accounting."""
    assert estimate_tokens("x") == 1


def test_the_estimate_scales_with_length() -> None:
    short = estimate_tokens("x" * 10)
    long = estimate_tokens("x" * 1000)
    assert long > short
    assert long == pytest.approx(1000 / CHARS_PER_TOKEN, abs=1)


def test_the_estimate_rounds_up() -> None:
    """Ceiling, not floor or round-to-nearest — the bias is toward
    overcounting (`tokens.py`'s module docstring), and rounding down would
    occasionally undercount a string whose true length lands just past a
    multiple of `CHARS_PER_TOKEN`."""
    text = "x" * 4  # 4 / 3.5 = 1.14..., must round up to 2, not down to 1
    assert estimate_tokens(text) == 2
