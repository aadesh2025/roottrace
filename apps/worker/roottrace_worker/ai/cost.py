"""Cost accounting, in whole micro-USD (`CLAUDE.md`: money is an integer,
never a float; `06` §8.1's typical-cost table is the source for the prices
below).

**Rates are stored per 1,000 tokens, not per token**, so a fast-tier price
like "$0.30 per 1M tokens" survives as the exact integer `300` (micro-USD
per 1,000 tokens) instead of truncating to `0` as a per-token rate would
(`$0.30 / 1_000_000 == 0.0000003`, which floors to zero micro-USD before it
is ever multiplied by a token count). The identity: `$X per 1M tokens ==
X * 1000` micro-USD per 1,000 tokens, since `$1 == 1_000_000` micro-USD.

Prices here are `06` §8.1's own "typical" numbers, not fetched from a
provider's live price page — a provider price change is a routing-config-
adjacent event this module should be updated alongside, not something a
stage discovers by calling out to a pricing API mid-request."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPrice:
    #: Micro-USD per 1,000 tokens — see module docstring for why this unit,
    #: not per-token, is what keeps sub-$1/1M-token rates exact.
    in_micro_usd_per_1k: int
    out_micro_usd_per_1k: int


#: Keyed by (provider, model). `06` §2.1's "Typical cost/1M in-out" column,
#: per tier, applied to the concrete models `models.yaml` currently routes
#: to that tier. A model not listed here is a config/pricing drift — see
#: `estimate_cost_micro_usd`'s `UnknownModelPricingError`.
PRICING: dict[tuple[str, str], ModelPrice] = {
    # fast: ~$0.30 / $1.50 per 1M
    ("anthropic", "claude-haiku-4-5"): ModelPrice(
        in_micro_usd_per_1k=300, out_micro_usd_per_1k=1_500
    ),
    ("openai", "gpt-4.1-mini"): ModelPrice(in_micro_usd_per_1k=300, out_micro_usd_per_1k=1_500),
    # reasoning-a / reasoning-b: ~$3 / $15 per 1M
    ("anthropic", "claude-sonnet-5"): ModelPrice(
        in_micro_usd_per_1k=3_000, out_micro_usd_per_1k=15_000
    ),
    ("openai", "gpt-5"): ModelPrice(in_micro_usd_per_1k=3_000, out_micro_usd_per_1k=15_000),
    # embed: ~$0.02 per 1M — input only, `tokens_out` is always 0 for embed calls.
    ("voyage", "voyage-code-3"): ModelPrice(in_micro_usd_per_1k=20, out_micro_usd_per_1k=0),
    ("openai", "text-embedding-3-large"): ModelPrice(
        in_micro_usd_per_1k=20, out_micro_usd_per_1k=0
    ),
}


class UnknownModelPricingError(Exception):
    """`(provider, model)` is not in `PRICING`. Raised rather than silently
    costing the call at zero — a free investigation is a billing bug, not a
    harmless default."""

    def __init__(self, provider: str, model: str):
        self.provider = provider
        self.model = model
        super().__init__(f"no pricing entry for {provider}:{model}")


def compute_cost_micro_usd(*, provider: str, model: str, tokens_in: int, tokens_out: int) -> int:
    """`04` §8's `cost_micro_usd` — a `bigint`, never a float, per
    `CLAUDE.md`. Cached input tokens are billed at the same rate as any
    other input token: `06` names provider-side prompt caching as a latency
    and *provider-cost* optimisation, not something this system's own
    accounting discounts — the tokens still occupied the context window.

    Integer division truncates rather than rounds, matching `06` §8.1's own
    framing of these as "typical" prices, not per-call provider invoices."""
    price = PRICING.get((provider, model))
    if price is None:
        raise UnknownModelPricingError(provider, model)
    return (tokens_in * price.in_micro_usd_per_1k + tokens_out * price.out_micro_usd_per_1k) // 1000
