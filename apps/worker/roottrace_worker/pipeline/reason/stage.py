"""S6 `reason` (`03` §S6) — T5.3.

Mirrors `understand.stage.understand`'s shape deliberately: call the
reasoner, treat its reply as untrusted, validate every claim against real
data, discard what does not bind, and never let a partially-hallucinated
reply reach a caller unfiltered. The one structural difference from S4 is
that S6 has no deterministic fallback — a reasoner failure (of any kind:
gateway exhaustion, or evidence binding failing twice) is a terminal
`insufficient_context`, not a "continue on the deterministic floor" path,
because there is no deterministic floor for root-cause reasoning."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from roottrace_worker.pipeline.reason.contracts import (
    BlastRadius,
    FixStrategy,
    IntroducedBy,
    RootCause,
    RootCauseAnalysis,
    TokenUsage,
)
from roottrace_worker.pipeline.reason.extraction_schema import ReasonReply
from roottrace_worker.pipeline.reason.reasoner import (
    ReasonerUnavailable,
    ReasonRequest,
    StructuredReasoner,
)
from roottrace_worker.pipeline.reason.validate import (
    primary_finding_survives,
    validate_eliminated_hypotheses,
    validate_reasoning_chain,
)
from roottrace_worker.pipeline.retrieve.bundle import ContextBundle, InsufficientContext
from roottrace_worker.pipeline.understand.contracts import ErrorUnderstanding


def _insufficient(bundle: ContextBundle, *, explanation: str) -> InsufficientContext:
    """`03` §S6: "terminal `insufficient_context`" on evidence-binding
    exhaustion. Reuses `retrieve.bundle.InsufficientContext` rather than a
    parallel S6-specific type — `insufficient_context` is a pipeline-wide
    terminal status (`04` §8's `investigation_status` enum has exactly one
    value for it, not one per stage), and the fields it already carries
    (admitted file count, admitted token count) are still the honest
    description of what evidence *was* available even though S6 could not
    produce a validated conclusion from it."""
    return InsufficientContext(
        bundle_id=bundle.bundle_id,
        repository=bundle.repository,
        admitted_file_count=len(bundle.files),
        admitted_in_app_tokens=bundle.token_count,
        explanation=explanation,
    )


class ReasonOutcome:
    """`RootCauseAnalysis` on success, `InsufficientContext` on failure —
    kept as an explicit wrapper (rather than a bare union return type)
    so `dropped_claims` — the S6 analogue of `understand`'s own
    `UnderstandOutcome.dropped_claims` — travels with a successful result
    without needing a third shape."""

    __slots__ = ("analysis", "dropped_claims", "insufficient")

    def __init__(
        self,
        *,
        analysis: RootCauseAnalysis | None = None,
        insufficient: InsufficientContext | None = None,
        dropped_claims: tuple[str, ...] = (),
    ) -> None:
        if (analysis is None) == (insufficient is None):
            raise ValueError("exactly one of analysis or insufficient must be set")
        self.analysis = analysis
        self.insufficient = insufficient
        self.dropped_claims = dropped_claims

    @property
    def ok(self) -> bool:
        return self.analysis is not None


async def reason(
    bundle: ContextBundle,
    understanding: ErrorUnderstanding,
    *,
    breadcrumbs: Sequence[Mapping[str, Any]] = (),
    reasoner: StructuredReasoner,
) -> ReasonOutcome:
    request = ReasonRequest(bundle=bundle, understanding=understanding, breadcrumbs=breadcrumbs)

    # Typed `Any` deliberately — the Protocol promises a `Mapping`, and the
    # check below would be dead code if that promise were trusted, but the
    # implementation behind this seam is a model reply, and S6 must never
    # crash on one (`understand/stage.py`'s identical reasoning).
    raw: Any
    try:
        raw = await reasoner.reason(request)
    except ReasonerUnavailable as exc:
        return ReasonOutcome(insufficient=_insufficient(bundle, explanation=str(exc)))

    if not isinstance(raw, Mapping):
        return ReasonOutcome(
            insufficient=_insufficient(bundle, explanation="reasoner returned a non-mapping reply")
        )

    reply = ReasonReply.model_validate(
        {k: v for k, v in raw.items() if k not in ("model", "prompt_version", "tokens")}
    )
    breadcrumb_count = len(breadcrumbs)

    kept_steps, chain_dropped = validate_reasoning_chain(
        reply.reasoning_chain, bundle=bundle, breadcrumb_count=breadcrumb_count
    )
    kept_hypotheses, hypothesis_dropped = validate_eliminated_hypotheses(
        reply.eliminated_hypotheses, bundle=bundle, breadcrumb_count=breadcrumb_count
    )

    if not primary_finding_survives(kept_steps, reply=reply, bundle=bundle):
        # The reasoner already ran this same check (and its own retry) before
        # returning — reached only if a reasoner implementation skipped that
        # discipline. The stage does not trust a reasoner's word for it either.
        return ReasonOutcome(
            insufficient=_insufficient(
                bundle, explanation="primary root-cause finding did not survive evidence binding"
            )
        )

    retrieved_paths = {file.repo_path for file in bundle.files}
    files_to_modify = tuple(p for p in reply.fix_strategy.files_to_modify if p in retrieved_paths)
    dropped_paths = tuple(
        f"fix_strategy.files_to_modify: {p!r} was never retrieved"
        for p in reply.fix_strategy.files_to_modify
        if p not in retrieved_paths
    )

    root_cause = RootCause(
        summary=reply.root_cause.summary,
        mechanism=reply.root_cause.mechanism,
        category=reply.root_cause.category,
        introduced_by=IntroducedBy(**reply.root_cause.introduced_by.model_dump())
        if reply.root_cause.introduced_by and reply.root_cause.introduced_by.commit
        else None,
        blast_radius=BlastRadius(**reply.root_cause.blast_radius.model_dump())
        if reply.root_cause.blast_radius
        else None,
    )
    fix_strategy = FixStrategy(
        approach=reply.fix_strategy.approach,
        files_to_modify=files_to_modify,
        must_not_modify=tuple(reply.fix_strategy.must_not_modify),
        considerations=tuple(reply.fix_strategy.considerations),
        regression_test_needed=reply.fix_strategy.regression_test_needed,
        regression_test_description=reply.fix_strategy.regression_test_description,
    )

    analysis = RootCauseAnalysis(
        root_cause=root_cause,
        reasoning_chain=kept_steps,
        eliminated_hypotheses=kept_hypotheses,
        fix_strategy=fix_strategy,
        self_assessed_confidence=reply.self_assessed_confidence,
        uncertainty_notes=tuple(reply.uncertainty_notes),
        model=str(raw.get("model", "unknown")),
        prompt_version=str(raw.get("prompt_version", "unknown")),
        tokens=TokenUsage(**raw.get("tokens", {})),
    )

    return ReasonOutcome(
        analysis=analysis, dropped_claims=chain_dropped + hypothesis_dropped + dropped_paths
    )
