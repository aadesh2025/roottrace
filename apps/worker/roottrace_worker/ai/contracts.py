"""The gateway's own contracts (`06` §2.4, T5.1).

`Tier` values are the hyphenated form used throughout `06` and
`infra/config/models.yaml` (`"reasoning-a"`, not `"reasoning_a"`) — the
Postgres enum `llm_tier` (`04` §8) spells the same four values with
underscores. The one conversion point is `LLMCallRecord.tier_db_value`,
mirroring the project's established camelCase/snake_case boundary-conversion
rule (`CLAUDE.md` conventions) rather than letting the two spellings drift
independently across the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

Tier = Literal["fast", "reasoning-a", "reasoning-b", "embed"]

#: `04` §8's `pipeline_stage` enum, spelled exactly as the DB stores it —
#: no hyphen/underscore boundary here, unlike `Tier`.
Stage = Literal[
    "receive",
    "fingerprint",
    "triage",
    "understand",
    "retrieve",
    "reason",
    "patch",
    "validate",
    "repair",
    "critique",
    "score",
    "publish",
    "await_decision",
    "feedback",
]

#: `06` §2.2's `failover.trigger_on`, verbatim.
FailoverTrigger = Literal["rate_limit", "timeout", "server_error", "content_filter"]


@dataclass(frozen=True, slots=True)
class RenderedPrompt:
    """The fully-assembled five-layer prompt (`06` §3.1), already fenced and
    scanned — `gateway.complete` does not know or care how the layers were
    built, only that L4 has already been marked untrusted by the caller.

    `system` and `user` mirror how every provider SDK actually wants the
    text split; L1-L3 map to `system`, L4-L5 map to `user`. `cache_key`
    covers L1-L3 (`06` §2.4: "Provider-side caching of the static L1/L2/L3/L5
    layers" — L5's schema text is static per stage too, so it travels with
    the system half here rather than needing a fourth prompt fragment)."""

    system: str
    user: str
    contains_untrusted_content: bool
    flagged_injection_patterns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LLMResult[T: BaseModel]:
    """What `LLMGateway.complete` hands back to a pipeline stage — the
    parsed, validated output plus everything `06` §8.3's cost-attribution
    section says must be knowable about the call that produced it."""

    output: T
    provider: str
    model: str
    tier: Tier
    tokens_in: int
    tokens_out: int
    cached_tokens_in: int
    cost_micro_usd: int
    latency_ms: int
    attempt: int
    prompt_version: str
    prompt_hash: str
    schema_repair_used: bool
    suspicious_content_detected: bool
    failover_from: str | None = None


@dataclass(frozen=True, slots=True)
class LLMCallRecord:
    """One `llm_calls` row (`04` §8), independent of the Pydantic
    `LLMResult` returned to the caller — this is what persists, that is what
    the stage consumes, and the two are allowed to diverge (e.g. `prompt_url`
    exists here and nowhere in `LLMResult`, since no pipeline stage needs its
    own prompt's storage location back)."""

    investigation_id: str | None
    project_id: str
    pipeline_step_id: str | None
    stage: Stage
    tier: Tier
    provider: str
    model: str
    prompt_version: str
    prompt_url: str
    response_url: str
    prompt_hash: str
    tokens_in: int
    tokens_out: int
    cost_micro_usd: int
    latency_ms: int
    attempt: int
    cached_tokens_in: int = 0
    failover_from: str | None = None
    schema_repair_used: bool = False
    suspicious_content_detected: bool = False

    #: `04` §8's `llm_tier` enum spelling — see module docstring.
    @property
    def tier_db_value(self) -> str:
        return self.tier.replace("-", "_")


@dataclass(frozen=True, slots=True)
class CompletionRequest[M: BaseModel]:
    """Everything `LLMGateway.complete` needs, gathered in one place so the
    orchestration methods it calls internally (routing, cache lookup, cost
    reservation, provider dispatch) share one argument rather than a growing
    positional list.

    Generic over the output model, so a caller building
    `CompletionRequest(output_model=Verdict, ...)` gets back
    `LLMResult[Verdict]` from `gateway.complete`, not a bare `BaseModel` it
    would otherwise have to narrow itself."""

    tier: Tier
    prompt: RenderedPrompt
    output_model: type[M]
    project_id: str
    stage: Stage
    prompt_version: str
    investigation_id: str | None = None
    pipeline_step_id: str | None = None
    max_repair_attempts: int = 1
    deterministic: bool = False
    now: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
