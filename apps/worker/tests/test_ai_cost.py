"""Cost accounting in whole micro-USD (`06` §8.1, T5.1)."""

from __future__ import annotations

import pytest

from roottrace_worker.ai.cost import UnknownModelPricingError, compute_cost_micro_usd

pytestmark = pytest.mark.unit


def test_a_dollar_per_1m_rate_survives_as_an_exact_integer() -> None:
    """`$0.30` per 1M input tokens must not truncate to `0` the way a naive
    per-token float would round — this is the regression this module's
    per-1,000-token unit exists to prevent."""
    cost = compute_cost_micro_usd(
        provider="anthropic", model="claude-haiku-4-5", tokens_in=1000, tokens_out=0
    )
    assert cost == 300


def test_output_tokens_use_the_output_rate() -> None:
    cost = compute_cost_micro_usd(
        provider="anthropic", model="claude-haiku-4-5", tokens_in=0, tokens_out=1000
    )
    assert cost == 1_500


def test_reasoning_tier_pricing() -> None:
    cost = compute_cost_micro_usd(
        provider="anthropic", model="claude-sonnet-5", tokens_in=19_000, tokens_out=2_100
    )
    # 19_000 * 3000/1000 + 2_100 * 15000/1000 = 57_000 + 31_500
    assert cost == 88_500


def test_an_unpriced_model_raises_rather_than_costing_zero() -> None:
    with pytest.raises(UnknownModelPricingError):
        compute_cost_micro_usd(
            provider="anthropic", model="claude-nonexistent", tokens_in=1, tokens_out=1
        )


def test_zero_tokens_costs_zero() -> None:
    assert (
        compute_cost_micro_usd(
            provider="anthropic", model="claude-haiku-4-5", tokens_in=0, tokens_out=0
        )
        == 0
    )
