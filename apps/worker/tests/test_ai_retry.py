"""Backoff and failover ordering (`06` §2.2, T5.1)."""

from __future__ import annotations

import random

import pytest

from roottrace_worker.ai.retry import compute_backoff_ms, should_fail_over
from roottrace_worker.ai.routing import BackoffConfig

pytestmark = pytest.mark.unit


def test_a_configured_trigger_fails_over() -> None:
    assert should_fail_over("rate_limit", configured_triggers=("rate_limit", "timeout"))


def test_an_unconfigured_trigger_does_not_fail_over() -> None:
    assert not should_fail_over("content_filter", configured_triggers=("rate_limit", "timeout"))


def test_backoff_grows_exponentially_without_jitter() -> None:
    config = BackoffConfig(base_ms=1000, factor=2, jitter=False, max_ms=16000)
    assert compute_backoff_ms(0, config) == 1000
    assert compute_backoff_ms(1, config) == 2000
    assert compute_backoff_ms(2, config) == 4000


def test_backoff_is_capped() -> None:
    config = BackoffConfig(base_ms=1000, factor=2, jitter=False, max_ms=3000)
    assert compute_backoff_ms(5, config) == 3000


def test_jitter_stays_within_the_uncapped_window() -> None:
    config = BackoffConfig(base_ms=1000, factor=2, jitter=True, max_ms=16000)
    rng = random.Random(42)  # noqa: S311 - deterministic seed for a test, not a security use
    value = compute_backoff_ms(1, config, rng=rng)
    assert 0 <= value <= 2000


def test_jitter_is_deterministic_given_the_same_rng_seed() -> None:
    config = BackoffConfig(base_ms=1000, factor=2, jitter=True, max_ms=16000)
    first = compute_backoff_ms(1, config, rng=random.Random(7))  # noqa: S311
    second = compute_backoff_ms(1, config, rng=random.Random(7))  # noqa: S311
    assert first == second


def test_negative_attempt_is_rejected() -> None:
    config = BackoffConfig(base_ms=1000, factor=2, jitter=False, max_ms=16000)
    with pytest.raises(ValueError, match="attempt"):
        compute_backoff_ms(-1, config)
