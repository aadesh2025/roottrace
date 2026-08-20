"""S10 `critique` (`03` §S10) — T7.2.

Mirrors `patch.stage.patch`'s shape for the untrusted-reply handling (a
non-mapping reply, or a reply that fails schema validation, is treated
the same as the critic being unavailable — never a crash), but has no
scope/applicability retry ladder of its own: `03` §S10 names two retries
on schema-level exhaustion (`06`'s generic three-attempt ladder already
covers that inside `LLMGateway.complete`) and no semantic retry beyond
it, unlike S6/S7's evidence-binding or diff-applicability correction
loops.

**No deterministic floor, and not terminal either — S10's own third
shape, distinct from every stage before it.** S4 has a real deterministic
pre-parse to fall back to; S9 has a real deterministic instruction text.
S10 has neither — there is no algorithmic substitute for an adversarial
code review — but `03` §S10 also explicitly forbids treating a gateway
failure as terminal for the whole investigation ("proceed to S11 with
`critic_component = 0`"). `CritiqueOutcome.unavailable` is that third
shape: not a value standing in for the review, and not a reason to stop,
just an honest "this did not happen" the caller (eventually S11) reads
directly."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from roottrace_worker.pipeline.critique.contracts import (
    Critique,
    CritiqueUnavailable,
    Finding,
    SecurityReview,
    TestQuality,
    TokenUsage,
)
from roottrace_worker.pipeline.critique.critic import (
    CriticUnavailable,
    CritiqueRequest,
    StructuredCritic,
)
from roottrace_worker.pipeline.critique.extraction_schema import CritiqueReply

_CALL_METADATA_KEYS = ("model", "prompt_version", "tokens")


class CritiqueOutcome:
    """`Critique` on a real review, `CritiqueUnavailable` when none could
    be produced — same exactly-one-of wrapper shape `reason.stage.
    ReasonOutcome` and `repair.stage.RepairOutcome` establish."""

    __slots__ = ("critique", "unavailable")

    def __init__(
        self,
        *,
        critique: Critique | None = None,
        unavailable: CritiqueUnavailable | None = None,
    ) -> None:
        if (critique is None) == (unavailable is None):
            raise ValueError("exactly one of critique or unavailable must be set")
        self.critique = critique
        self.unavailable = unavailable


def _is_blocking(reply: CritiqueReply) -> bool:
    """`03` §S10: "A `reject` verdict, or any `severity: critical`
    finding, is blocking." Computed here, not read from the model's own
    `blocking` field — `contracts.py`'s module docstring has the reasoning."""
    return reply.verdict == "reject" or any(f.severity == "critical" for f in reply.findings)


async def critique(
    request: CritiqueRequest,
    *,
    critic: StructuredCritic,
    critique_id: str,
) -> CritiqueOutcome:
    """Run S10 over one patch and its validation result. `critique_id` is
    caller-supplied, same precedent `patch.stage.patch`'s `patch_id` and
    `repair.stage.repair`'s `repair_id` set."""
    reply: Any
    try:
        reply = await critic.critique(request)
    except CriticUnavailable as exc:
        return CritiqueOutcome(
            unavailable=CritiqueUnavailable(critique_id=critique_id, reason=str(exc))
        )

    # Typed `Any` deliberately, same reasoning `patch.stage.patch` gives
    # for its own identical check — the Protocol promises a `Mapping`,
    # but the implementation behind this seam is a model reply parsed by
    # a gateway, and this stage must never crash on one.
    if not isinstance(reply, Mapping):
        return CritiqueOutcome(
            unavailable=CritiqueUnavailable(
                critique_id=critique_id, reason="critic returned a non-mapping reply"
            )
        )

    call_meta = {k: reply.get(k) for k in _CALL_METADATA_KEYS}
    try:
        parsed = CritiqueReply.model_validate(
            {k: v for k, v in reply.items() if k not in call_meta}
        )
    except Exception as exc:
        return CritiqueOutcome(
            unavailable=CritiqueUnavailable(
                critique_id=critique_id, reason=f"critic reply failed schema validation: {exc}"
            )
        )

    concerns = parsed.security_review.concerns
    tokens_raw = call_meta.get("tokens") or {}

    return CritiqueOutcome(
        critique=Critique(
            critique_id=critique_id,
            verdict=parsed.verdict,
            agreement_with_diagnosis=parsed.agreement_with_diagnosis,
            addresses_reported_error=parsed.addresses_reported_error,
            findings=tuple(
                # `CritiqueFinding`/`CritiqueFindingEvidence` (loose, model-facing)
                # and `Finding`/`FindingEvidence` (trusted) are structurally
                # identical — no validation step narrows one into the other
                # the way `patch.stage.patch` does for `FileChange`, since
                # nothing here is computed from the diff the way file stats are.
                Finding.model_validate(f.model_dump())
                for f in parsed.findings
            ),
            security_review=SecurityReview(concerns=concerns, clean=not concerns),
            regression_risk=parsed.regression_risk,
            test_quality=TestQuality(**parsed.test_quality.model_dump()),
            scope_assessment=parsed.scope_assessment,
            blocking=_is_blocking(parsed),
            model=str(call_meta.get("model") or "unknown"),
            prompt_version=str(call_meta.get("prompt_version") or "unknown"),
            tokens=TokenUsage(**tokens_raw) if isinstance(tokens_raw, dict) else TokenUsage(),
        )
    )
