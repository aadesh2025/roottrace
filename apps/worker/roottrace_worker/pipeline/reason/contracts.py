"""The `RootCauseAnalysis` contract (`03` §S6 "Output contract") — T5.3.

Pydantic, same reasoning as `understand.contracts.ErrorUnderstanding` and
`retrieve.bundle.ContextBundle`: this is a stage boundary whose producer is
a language model (`03` §1 R4), and `frozen=True, extra="forbid"` for the
same two reasons — S7 (patch) binds to these values by literal comparison,
and a model inventing a field is not producing the contract.

**Every `Evidence` entry here has already survived `validate.py`'s binding
check by the time it reaches this contract.** `RootCauseAnalysis` is what
`stage.py` returns *after* discarding unsupported claims — it is never
constructed directly from a raw model reply. The loose, permissive schema
the model actually sees and is initially validated against is
`extraction_schema.ReasonReply`, deliberately separate (same split T5.2
established for `UnderstandExtractionReply` vs. `ErrorUnderstanding`)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceKind = Literal["file", "breadcrumb", "commit"]
StepType = Literal["observe", "hypothesise", "test", "chain", "conclude"]
RootCauseCategory = Literal[
    "unhandled_error_path",
    "external_dependency",
    "logic_error",
    "race_condition",
    "resource_exhaustion",
    "configuration",
    "data_integrity",
    "other",
]


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Evidence(_Contract):
    """One bound artefact reference (`03` §S6's "Hard rule"). Exactly one of
    `repo_path`/`line_range`/`excerpt` (kind `"file"`), `index` (kind
    `"breadcrumb"`), or `sha` (kind `"commit"`) is populated, matching
    `kind` — `validate.py` is what enforces that shape, not this contract,
    since the raw reply this is built from is looser (`ReasonReply`)."""

    kind: EvidenceKind
    repo_path: str | None = None
    line_range: tuple[int, int] | None = None
    excerpt: str | None = None
    index: int | None = None
    sha: str | None = None


class ReasoningStep(_Contract):
    step: int = Field(ge=1)
    type: StepType
    statement: str
    prior: float | None = Field(default=None, ge=0.0, le=1.0)
    supports: tuple[int, ...] = ()
    evidence: tuple[Evidence, ...] = ()


class EliminatedHypothesis(_Contract):
    statement: str
    eliminated_because: str
    evidence: tuple[Evidence, ...] = ()


class IntroducedBy(_Contract):
    commit: str
    date: str | None = None
    author: str | None = None
    note: str | None = None


class BlastRadius(_Contract):
    affected_endpoints: tuple[str, ...] = ()
    affected_functions: tuple[str, ...] = ()
    other_callers_at_risk: tuple[str, ...] = ()
    severity_justification: str | None = None


class RootCause(_Contract):
    summary: str
    mechanism: str
    category: RootCauseCategory
    introduced_by: IntroducedBy | None = None
    blast_radius: BlastRadius | None = None


class FixStrategy(_Contract):
    approach: str
    files_to_modify: tuple[str, ...] = ()
    must_not_modify: tuple[str, ...] = ()
    considerations: tuple[str, ...] = ()
    regression_test_needed: bool = True
    regression_test_description: str | None = None


class TokenUsage(_Contract):
    prompt: int = 0
    completion: int = 0


class RootCauseAnalysis(_Contract):
    root_cause: RootCause
    reasoning_chain: tuple[ReasoningStep, ...]
    eliminated_hypotheses: tuple[EliminatedHypothesis, ...] = ()
    fix_strategy: FixStrategy
    #: `03` §S11: recorded, weighted at only 10% of the final score
    #: (`06` §7.1) — real signals from execution dominate. (Corrected at
    #: T7.3 from a stale "15%" written before `score.stage.py` existed to
    #: check the real formula against.)
    self_assessed_confidence: float = Field(ge=0.0, le=1.0)
    uncertainty_notes: tuple[str, ...] = ()
    model: str
    prompt_version: str
    tokens: TokenUsage = Field(default_factory=TokenUsage)
