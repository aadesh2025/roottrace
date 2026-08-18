"""The LLM-facing schema for S4's structured-extraction call (`03` §S4
output contract, `A2` §3, T5.2).

**Deliberately separate from `contracts.py`'s `ErrorUnderstanding` and its
frozen, `extra="forbid"` sub-models.** This schema exists only to let
`gateway.py`'s structured-output ladder (`06` §4.1) check that a reply is
*roughly the right shape* — the real, authoritative validation is
`validate.py`'s `apply_extraction`, which works from the raw dict this
model's `.model_dump()` produces and applies the actual semantic rules
(`06` §4.2's two-layer validation: schema first, semantics second). A
model reply is untrusted regardless of which schema it satisfied, so this
one is intentionally loose — extra fields ignored, most fields optional —
rather than a second copy of `contracts.py`'s strict shapes that a slightly
different but harmless reply would fail for no reason worth failing over.

`entry_point`/`failure_point` are included because `03` §S4's literal
output contract shows them, but `apply_extraction` never reads them from
the reply — S4's own deterministic pre-parse already computes both
mechanically (`plan.py`), and `03` line "may not ... contradict the
runtime metadata" is exactly why a model's opinion on them is never
merged. They exist here so the schema shown to the model matches `03`'s
contract faithfully, not because anything downstream consumes them."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ExtractionFrameAssessment(_Loose):
    index: int
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractionHypothesis(_Loose):
    statement: str
    prior: float = Field(ge=0.0, le=1.0)
    evidence_needed: list[str] = Field(default_factory=list)


class ExtractionRetrievalPlan(_Loose):
    must_fetch: list[str] = Field(default_factory=list)
    should_fetch_by_symbol: list[str] = Field(default_factory=list)
    semantic_queries: list[str] = Field(default_factory=list)
    want_git_history_for: list[str] = Field(default_factory=list)
    want_tests_for: list[str] = Field(default_factory=list)
    breadcrumb_signal: str | None = None


class ExtractionExceptionInfo(_Loose):
    family: str | None = None


class ExtractionEntryPoint(_Loose):
    """Informational only — see module docstring."""

    type: str | None = None
    method: str | None = None
    pattern: str | None = None
    handler: str | None = None


class ExtractionFailurePoint(_Loose):
    """Informational only — see module docstring."""

    repo_path: str | None = None
    function: str | None = None
    line: int | None = None


class UnderstandExtractionReply(_Loose):
    """`03` §S4's output contract. Every field optional except none —
    `apply_extraction` tolerates a completely empty reply (it merges
    nothing and the deterministic pre-parse stands unchanged), so the
    schema should too rather than forcing a model to invent content for a
    field it has nothing to say about."""

    exception: ExtractionExceptionInfo | None = None
    frames: list[ExtractionFrameAssessment] = Field(default_factory=list)
    entry_point: ExtractionEntryPoint | None = None
    failure_point: ExtractionFailurePoint | None = None
    implicated_symbols: list[str] = Field(default_factory=list)
    initial_hypotheses: list[ExtractionHypothesis] = Field(default_factory=list)
    retrieval_plan: ExtractionRetrievalPlan | None = None
    notes: str | None = None
    extraction_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    suspicious_content_detected: bool = False
