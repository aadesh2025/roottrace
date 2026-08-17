"""The seam where the LLM extraction plugs in (`03` §S4 step 2).

S4's algorithm is three steps — deterministic pre-parse, LLM structured
extraction, deterministic post-validation. The gateway that makes the middle
step possible is **T5.1**, and the prompt system is **T5.2**, both in Phase 8;
retrieval is Phase 7 and comes first (`15` §2, which is canonical and says not
to skip ahead). So the middle step is a Protocol with one implementation today.

This is not a stub standing in for missing work. `03` §S4 requires exactly this
behaviour on the LLM's *own* failure paths:

> LLM returns invalid JSON twice → fall back to deterministic pre-parse only,
> `extraction_confidence: 0.5`, continue.
>
> **On exhaustion:** fall back to the deterministic pre-parse with
> `extraction_confidence: 0.5` and continue — **never terminal.**

`UnavailableExtractor` takes that path on the first call instead of the third.
Every consequence of it — the fallback plan, the empty hypothesis list, the
0.5 confidence, the `deterministic_only` flag — is a path production will take
whenever a provider is down, so it is exercised continuously rather than only
in the incident it was written for.

**What T5.2 changes here: nothing but the binding.** It adds an
implementation that calls the gateway with `understand/v3.md`, and passes it to
`understand(...)`. `stage.py`, `validate.py`, the contracts and the plan are
untouched.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from roottrace_worker.pipeline.understand.contracts import ExceptionFamily, Frame


class ExtractorUnavailable(Exception):
    """No structured extraction was produced, for any reason.

    Deliberately one exception rather than a hierarchy of provider-down,
    timed-out, budget-exhausted and malformed-twice. The stage's response to
    every one of them is identical — take the deterministic pre-parse and
    continue — and a caller that could distinguish them would eventually grow a
    branch that treats one of them as fatal, which `03` §S4 forbids.
    """


class ExtractionRequest:
    """Everything the extractor is allowed to see.

    An explicit object rather than the raw event, because the raw event carries
    fields the prompt must never receive — and because what goes into a prompt
    should be reviewable in one place. `sanitise.py` has already redacted the
    payload at ingest; this narrows it further to the fields `A2` §3 names.
    """

    __slots__ = (
        "breadcrumbs",
        "exception_message",
        "exception_type",
        "family",
        "frames",
        "framework",
        "language",
        "request",
    )

    def __init__(
        self,
        *,
        language: str | None,
        framework: str | None,
        exception_type: str,
        exception_message: str,
        family: ExceptionFamily,
        frames: tuple[Frame, ...],
        breadcrumbs: tuple[Mapping[str, Any], ...] = (),
        request: Mapping[str, Any] | None = None,
    ) -> None:
        self.language = language
        self.framework = framework
        self.exception_type = exception_type
        self.exception_message = exception_message
        self.family = family
        self.frames = frames
        self.breadcrumbs = breadcrumbs
        self.request = request


@runtime_checkable
class StructuredExtractor(Protocol):
    """Turns the pre-parse into the model-authored half of the understanding.

    Returns a plain mapping, not an `ErrorUnderstanding`. The reply is untrusted
    until `validate.py` has checked it against the pre-parse — a contract type
    would imply it had already been vetted, and Pydantic would have coerced
    away exactly the malformations worth catching.
    """

    async def extract(self, request: ExtractionRequest) -> Mapping[str, Any]:
        """Raise `ExtractorUnavailable` rather than returning a partial reply."""
        ...


class UnavailableExtractor:
    """The V1 extractor: there isn't one yet, and it says so.

    Raising beats returning an empty mapping. An empty mapping would merge
    cleanly, produce an understanding indistinguishable from a successful
    extraction that happened to find nothing, and report
    `extraction_confidence` from a model that was never called.
    """

    async def extract(self, request: ExtractionRequest) -> Mapping[str, Any]:
        raise ExtractorUnavailable(
            "no structured extractor is configured; the LLM gateway is T5.1 and the "
            "understand/v3.md prompt is T5.2. Falling back to the deterministic "
            "pre-parse, per 03 §S4."
        )
