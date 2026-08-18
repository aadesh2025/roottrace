"""Gateway failures.

Same discipline as `github/errors.py`: every provider raises one of these,
never its own SDK's exception type, so nothing above the provider seam is
provider-aware. Codes are the registered `RT-AI-*` / `RT-QUOTA-*` range
(`17` §4) — nothing here invents one.
"""

from __future__ import annotations

from roottrace_worker.ai.contracts import FailoverTrigger


class LLMError(Exception):
    """Base for every gateway failure. Carries no code of its own — every
    concrete subclass below maps to a specific registered code; a caller
    that catches this base type is choosing not to distinguish them."""


class ProviderError(LLMError):
    """A single provider call failed in a way `06` §2.2's failover list
    recognises. Internal to the gateway's retry/failover loop — never raised
    past it, so it carries no registered code of its own; what the caller
    ultimately sees, if every provider in the tier fails this way, is
    `AllProvidersExhaustedError`."""

    def __init__(self, provider: str, trigger: FailoverTrigger, detail: str):
        self.provider = provider
        self.trigger = trigger
        super().__init__(f"{provider}: {trigger} — {detail}")


class AllProvidersExhaustedError(LLMError):
    """Every provider in the tier failed over, in order, and none succeeded.

    `06` §2.2 caps `max_provider_attempts` per provider precisely so this is
    reachable rather than an infinite loop."""

    code = "RT-AI-0001"

    def __init__(self, tier: str, attempts: list[ProviderError]):
        self.tier = tier
        self.attempts = attempts
        super().__init__(
            f"tier {tier!r} exhausted after {len(attempts)} attempt(s): "
            + "; ".join(str(a) for a in attempts)
        )


class SchemaValidationFailedError(LLMError):
    """The three-attempt ladder (`06` §4.1) ran out: native call, repair
    call, and deterministic salvage all failed to produce output the
    `output_model` accepts. No partial output is ever returned — the same
    rule `06` §4.1 states for this exact failure."""

    code = "RT-AI-0003"

    def __init__(self, output_model: str, last_error: str):
        self.output_model = output_model
        self.last_error = last_error
        super().__init__(f"{output_model}: schema validation failed — {last_error}")


class SuspiciousContentRejectedError(LLMError):
    """`06` §3.2's output-side check: the model's response echoed a flagged
    injection string. Raised only after the one allowed retry also failed
    the check — the retry itself is not an error, this is what happens when
    it does not help."""

    code = "RT-AI-0007"

    def __init__(self, pattern: str):
        self.pattern = pattern
        super().__init__(f"response rejected after retry: matched {pattern!r}")


class QuotaExhaustedError(LLMError):
    """`06` §8.2a's B9 breaker: the project's daily or monthly cost cap has
    no room left for even the pessimistic reservation estimate."""

    code = "RT-QUOTA-0002"

    def __init__(self, project_id: str, reason: str):
        self.project_id = project_id
        self.reason = reason
        super().__init__(f"project {project_id}: quota exhausted ({reason})")
