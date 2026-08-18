"""The LLM-facing schema for S6's reasoning call (`03` §S6 output contract,
`A2` §4, T5.3).

**Deliberately separate from `contracts.py`'s frozen, `extra="forbid"`
`RootCauseAnalysis`** — same split T5.2 established for `understand`
(`extraction_schema.UnderstandExtractionReply` vs.
`contracts.ErrorUnderstanding`). This is what the gateway's structured-
output ladder (`06` §4.1) validates against; the real validation —
`validate.py`'s evidence-binding check against the actual `ContextBundle`
(`06` §4.2's two-layer split) — happens after, on the raw dict this
model's `.model_dump()` produces. A model reply is untrusted regardless of
which schema it satisfied."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from roottrace_worker.pipeline.reason.contracts import EvidenceKind, RootCauseCategory, StepType


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ReasonEvidence(_Loose):
    kind: EvidenceKind
    repo_path: str | None = None
    line_range: tuple[int, int] | None = None
    excerpt: str | None = None
    index: int | None = None
    sha: str | None = None


class ReasonStep(_Loose):
    step: int = 0
    type: StepType = "observe"
    statement: str = ""
    prior: float | None = Field(default=None, ge=0.0, le=1.0)
    supports: list[int] = Field(default_factory=list)
    evidence: list[ReasonEvidence] = Field(default_factory=list)


class ReasonEliminatedHypothesis(_Loose):
    statement: str = ""
    eliminated_because: str = ""
    evidence: list[ReasonEvidence] = Field(default_factory=list)


class ReasonIntroducedBy(_Loose):
    commit: str | None = None
    date: str | None = None
    author: str | None = None
    note: str | None = None


class ReasonBlastRadius(_Loose):
    affected_endpoints: list[str] = Field(default_factory=list)
    affected_functions: list[str] = Field(default_factory=list)
    other_callers_at_risk: list[str] = Field(default_factory=list)
    severity_justification: str | None = None


class ReasonRootCause(_Loose):
    summary: str = ""
    mechanism: str = ""
    category: RootCauseCategory = "other"
    introduced_by: ReasonIntroducedBy | None = None
    blast_radius: ReasonBlastRadius | None = None


class ReasonFixStrategy(_Loose):
    approach: str = ""
    files_to_modify: list[str] = Field(default_factory=list)
    must_not_modify: list[str] = Field(default_factory=list)
    considerations: list[str] = Field(default_factory=list)
    regression_test_needed: bool = True
    regression_test_description: str | None = None


class ReasonReply(_Loose):
    """`03` §S6's output contract, loosely typed. Required fields are kept
    to the minimum `validate.py` cannot proceed without — `root_cause` and
    `reasoning_chain` — since a reply missing everything else is still
    processable (empty `eliminated_hypotheses`, a generic `fix_strategy`),
    but a reply with no claimed root cause and no reasoning at all is not
    a reasoning failure the retry ladder can usefully correct; it is a
    reply that never engaged with the task."""

    root_cause: ReasonRootCause
    reasoning_chain: list[ReasonStep] = Field(default_factory=list)
    eliminated_hypotheses: list[ReasonEliminatedHypothesis] = Field(default_factory=list)
    fix_strategy: ReasonFixStrategy = Field(default_factory=ReasonFixStrategy)
    self_assessed_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    uncertainty_notes: list[str] = Field(default_factory=list)
    suspicious_content_detected: bool = False
