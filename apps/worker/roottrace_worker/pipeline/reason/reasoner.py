"""The seam where S6's reasoning call plugs in (`03` §S6) — same shape as
`understand.extractor.StructuredExtractor`, for the same reason: a
`Protocol` a test double satisfies structurally, not a subclass standing
in for one.

**Unlike S4, there is no historical "unavailable" era to model here.** T4.1
needed `UnavailableExtractor` because the gateway (T5.1) and prompt system
(T5.2) did not exist yet when S4 was built, and `03` §S4's own failure-mode
table gives that exact fallback a spec-mandated shape. S6 has no
deterministic fallback to fall back *to* — root-cause reasoning cannot
happen without a model — and T5.1/T5.2 already exist by the time this
ticket starts, so `GatewayReasoner` is the only implementation this seam
ever needs in V1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from roottrace_worker.pipeline.retrieve.bundle import ContextBundle
from roottrace_worker.pipeline.understand.contracts import ErrorUnderstanding


class ReasonerUnavailable(Exception):
    """No root-cause analysis was produced, for any reason — the gateway
    exhausted every provider, or the evidence-binding retry (`03` §S6)
    also failed on its second attempt. Deliberately one exception, not a
    hierarchy: `stage.py`'s response to every cause is identical — the
    investigation terminates as `insufficient_context` — matching
    `ExtractorUnavailable`'s exact reasoning."""


class ReasonRequest:
    """Everything the reasoner is allowed to see. An explicit object,
    same reasoning as `understand.extractor.ExtractionRequest`: what goes
    into a prompt should be reviewable in one place, not reconstructed
    from whatever fields happen to be lying around."""

    __slots__ = ("breadcrumbs", "bundle", "understanding")

    def __init__(
        self,
        *,
        bundle: ContextBundle,
        understanding: ErrorUnderstanding,
        breadcrumbs: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self.bundle = bundle
        self.understanding = understanding
        self.breadcrumbs = tuple(breadcrumbs)


@runtime_checkable
class StructuredReasoner(Protocol):
    """Turns a context bundle into the model-authored root-cause analysis.

    Returns a plain mapping, not a `RootCauseAnalysis` — the reply is
    untrusted until `validate.py` has bound every claim to the bundle by
    literal comparison, same reasoning `StructuredExtractor` documents for
    the identical design choice."""

    async def reason(self, request: ReasonRequest) -> Mapping[str, Any]:
        """Raise `ReasonerUnavailable` rather than returning a partial reply."""
        ...
