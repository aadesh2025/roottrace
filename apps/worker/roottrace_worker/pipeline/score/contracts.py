"""S11 `score` (`03` §S11) — T7.3's trusted output contract, literal from
`03`'s worked example. No `model`/`prompt_version`/`tokens` fields at
all — `03` §S11 is explicit: "Pure computation, no LLM," the only stage
in the pipeline with that property (`06` §7 has none of the cost-model
rows T5.1-T7.2 all carry)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Band = Literal["high", "medium", "low", "insufficient"]
PublishMode = Literal["open_pr", "open_draft_pr", "analysis_only"]
#: `03` §S11's six named components, in formula order.
ComponentName = Literal[
    "validation", "critic", "retrieval", "evidence", "self_assessment", "historical"
]
#: The five named hard-gate conditions `03` §S11's own table gives, plus
#: none — `gates_applied` lists whichever of these actually fired.
GateName = Literal[
    "build_passed_false",
    "critic_reject",
    "critical_security_finding",
    "regression_test_invalid",
    "retrieval_quality_low",
]


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ComponentBreakdown(_Contract):
    weight: float = Field(ge=0.0, le=1.0)
    raw: float = Field(ge=0.0, le=1.0)
    contribution: float = Field(ge=0.0, le=1.0)


class Breakdown(_Contract):
    validation: ComponentBreakdown
    critic: ComponentBreakdown
    retrieval: ComponentBreakdown
    evidence: ComponentBreakdown
    self_assessment: ComponentBreakdown
    historical: ComponentBreakdown


class ConfidenceScore(_Contract):
    confidence: float = Field(ge=0.0, le=1.0)
    band: Band
    breakdown: Breakdown
    gates_applied: tuple[GateName, ...] = ()
    explanation: str
    should_publish: bool
    publish_mode: PublishMode
    auto_merge_eligible: bool
