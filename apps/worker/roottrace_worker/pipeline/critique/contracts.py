"""S10 `critique` (`03` §S10) — T7.2's trusted output contract, literal
from `03`'s worked example. `model`/`prompt_version` are caller-computed
from the real `LLMResult`, never trusted from the model's own JSON — the
same rule every other Gateway-backed stage in this package follows.

**`blocking` is deterministically computed by `stage.py`, never trusted
from the model's own reply**, even though `A2` §6's task layer instructs
the model to set it itself ("Set `blocking: true` for any critical
security concern or a `reject` verdict"). `verdict`/`findings[].severity`
are already part of the same reply, and whether either condition holds
is a fact this stage can check directly — trusting a second, redundant
self-report of a fact already computable is exactly what `patch.stage.
patch`'s own docstring warns against ("the stage does not trust a
patcher's word for it either"). Same reasoning for `security_review.
clean`: computed as `not concerns`, not read from the model's own flag,
so a reply that lists concerns but also claims `clean: true` cannot slip
through as a contradiction nobody caught."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["approve", "approve_with_notes", "request_changes", "reject"]
#: `03` §S11's own `critic_component` formula deducts "-0.15 per HIGH
#: finding, -0.05 per MEDIUM finding" and `03` §S10's blocking rule names
#: `critical` explicitly — the four-level scale both sections imply.
Severity = Literal["low", "medium", "high", "critical"]
#: `A2` §6's seven numbered review dimensions, snake_cased to match the
#: `03` §S10 worked example's own `"dimension": "completeness"` spelling.
Dimension = Literal[
    "correctness",
    "completeness",
    "regression_risk",
    "security",
    "scope",
    "test_quality",
    "style",
]
RiskLevel = Literal["low", "medium", "high"]


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FindingEvidence(_Contract):
    """A pointer into the retrieved bundle for a human reviewer to jump
    to — deliberately *not* run through `reason.validate`'s literal-
    string evidence-binding check. `03` §S10's own worked example gives
    this shape as `{repo_path, line_range}` with no `excerpt` field at
    all, unlike `reason.contracts.Evidence`'s four-kind, excerpt-checked
    shape (`03` §S6's own "Hard rule — evidence binding", written inside
    the S6 section specifically). Binding critique findings the same way
    would need inventing an excerpt-citation format the model was never
    asked to produce — no `15` T7.2 accept criterion asks for it, and
    building it anyway would be the unrequested rigor `CLAUDE.md` warns
    against. Disclosed, not silently skipped — see `PROJECT-STATUS.md`'s
    T7.2 section."""

    repo_path: str
    line_range: tuple[int, int] | None = None


class Finding(_Contract):
    severity: Severity
    dimension: Dimension
    statement: str
    evidence: FindingEvidence | None = None
    recommendation: str | None = None


class SecurityReview(_Contract):
    concerns: tuple[str, ...] = ()
    clean: bool = True


class TestQuality(_Contract):
    reproduces_bug: bool
    assessment: str


class TokenUsage(_Contract):
    prompt: int = 0
    completion: int = 0


class Critique(_Contract):
    critique_id: str
    verdict: Verdict
    agreement_with_diagnosis: float = Field(ge=0.0, le=1.0)
    addresses_reported_error: bool
    findings: tuple[Finding, ...] = ()
    security_review: SecurityReview
    regression_risk: RiskLevel
    test_quality: TestQuality
    scope_assessment: str
    blocking: bool
    model: str
    prompt_version: str
    tokens: TokenUsage = Field(default_factory=TokenUsage)


class CritiqueUnavailable(_Contract):
    """`03` §S10: "On exhaustion: proceed to S11 with `critic_component =
    0` and a visible 'review unavailable' banner — never silently treated
    as approval." Unlike S6/S7 (a gateway failure is terminal for the
    whole investigation) and unlike S9 (a gateway failure degrades to a
    real deterministic floor), S10 has no deterministic review to fall
    back to — there is no algorithmic substitute for an adversarial code
    review — so a gateway failure here is neither terminal nor silently
    absorbed: it becomes its own honest, visible outcome."""

    critique_id: str
    reason: str
