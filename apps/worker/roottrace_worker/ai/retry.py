"""Backoff and failover ordering (`06` §2.2).

Pure functions — no `asyncio.sleep`, no provider calls. `gateway.py` is what
actually waits and dispatches; keeping the arithmetic here means the backoff
curve and the failover decision are each one deterministic function a unit
test can call directly, not something only observable by timing a real
retry loop (`14`'s "no `sleep()` in tests" rule, honoured by construction
rather than by discipline)."""

from __future__ import annotations

import random

from roottrace_worker.ai.routing import BackoffConfig


def should_fail_over(trigger: str, *, configured_triggers: tuple[str, ...]) -> bool:
    """`06` §2.2: `failover.trigger_on`. A trigger not in that list — the
    config's own way of saying "do not fail over for this" — is not a
    failover-worthy failure; `gateway.py` re-raises it immediately rather
    than burning a provider attempt on a class of error the config never
    named."""
    return trigger in configured_triggers


def compute_backoff_ms(
    attempt: int, config: BackoffConfig, *, rng: random.Random | None = None
) -> int:
    """`06` §2.2: `base_ms * factor^attempt`, capped at `max_ms`, jittered.

    `attempt` is zero-based (the delay *before* the first retry, i.e. after
    the first failure, is `attempt=0`). `rng` is injectable so a test can
    assert an exact value instead of only a range."""
    if attempt < 0:
        raise ValueError("attempt must be >= 0")
    raw = config.base_ms * (config.factor**attempt)
    capped = min(raw, config.max_ms)
    if not config.jitter:
        return int(capped)
    generator = rng or random.Random()  # noqa: S311 - jitter, not a security use
    # Full jitter: uniform(0, capped) — spreads retries across the window
    # instead of every client backing off in lockstep on a shared outage.
    return int(generator.uniform(0, capped))
