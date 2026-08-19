"""S9 `repair` (`03` §S9) — T7.1's trusted output contract, literal from
`03`'s worked example: `repair_id`, `attempt`, `failed_gate`, `strategy`,
`reroute_to_stage`, `instruction_delta`, `previous_attempts_summary`. No
`model`/`prompt_version`/`tokens` fields — unlike S6/S7, `03` §S9's own
JSON example never shows them, and `04`'s data model agrees: there is no
dedicated repair-attempts table with call-metadata columns, only
`investigations.repair_attempts` (a counter) and `validation_runs.
repair_hint` (one text column). The LLM call itself is still recorded —
every `LLMGateway.complete()` call writes its own `llm_calls` row
regardless of which stage made it (`06` §3.3) — S9's own contract just
never needed to carry that provenance a second time."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

#: `03` §S9's routing table has exactly one row per gate — a model is
#: never asked to choose either of these; `routing.py` computes them
#: deterministically from `failed_gate` alone.
Strategy = Literal[
    "targeted_syntax_fix",
    "use_available_imports",
    "fix_compile_error",
    "regenerate_test_only",
    "reconsider_root_cause",
    "preserve_existing_contract",
    "remediate_static_findings",
    "remediate_security_construct",
]
RerouteStage = Literal["S6", "S7"]


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PreviousAttempt(_Contract):
    attempt: int = Field(ge=1)
    failed_gate: str
    reason: str


class RepairDecision(_Contract):
    repair_id: str
    attempt: int = Field(ge=1)
    failed_gate: str
    strategy: Strategy
    reroute_to_stage: RerouteStage
    instruction_delta: str
    previous_attempts_summary: tuple[PreviousAttempt, ...] = ()


class RepairExhausted(_Contract):
    """Terminal `validation_failed` (`04`'s `investigation_status` enum) —
    `03` §S9: "preserve every attempt for inspection." `attempts_summary`
    includes the just-exhausted attempt, not just the ones before it —
    the same shape `RepairDecision.previous_attempts_summary` would have
    carried had one more attempt been allowed."""

    repair_id: str
    attempt: int = Field(ge=1)
    failed_gate: str
    attempts_summary: tuple[PreviousAttempt, ...]
