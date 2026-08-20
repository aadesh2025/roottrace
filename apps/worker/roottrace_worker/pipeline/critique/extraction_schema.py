"""The model's own JSON reply shape for S10 — loose, checked against
`contracts.py`'s trusted shape by `stage.py`, same two-model split T5.3/
T5.4 establish. `blocking` and `security_review.clean` are accepted here
(the model is asked to produce them, per `A2` §6's task layer) but never
propagated into `Critique` — `contracts.py`'s module docstring has the
reasoning: both are redundant, independently computable facts, and this
stage recomputes rather than trusts a model's self-report of either."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from roottrace_worker.pipeline.critique.contracts import Dimension, RiskLevel, Severity, Verdict


class CritiqueFindingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_path: str
    line_range: tuple[int, int] | None = None


class CritiqueFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Severity
    dimension: Dimension
    statement: str
    evidence: CritiqueFindingEvidence | None = None
    recommendation: str | None = None


class CritiqueSecurityReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concerns: tuple[str, ...] = ()
    clean: bool = True


class CritiqueTestQuality(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reproduces_bug: bool
    assessment: str


class CritiqueReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    agreement_with_diagnosis: float = Field(ge=0.0, le=1.0)
    addresses_reported_error: bool
    findings: tuple[CritiqueFinding, ...] = ()
    security_review: CritiqueSecurityReview
    regression_risk: RiskLevel
    test_quality: CritiqueTestQuality
    scope_assessment: str
    blocking: bool = False
