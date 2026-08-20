"""The seam where S10's independent-review LLM call plugs in (`03` §S10).

**`CritiqueRequest` only ever carries `diff: str`, never a `Patch`
object.** `03` §S10 is explicit about what the critic must not see:
"S7's explanation or self-assessment." A `Patch` object carries exactly
those fields (`explanation`, `risk_assessment`, `alternatives_
considered`) alongside the diff a critique legitimately needs — passing
the whole object and merely *remembering* not to render three of its
fields is the kind of discipline that erodes the first time someone adds
a new field to `Patch` without checking this call site. Accepting only
the diff text makes the omission structural, the same reasoning
`understand.extractor.ExtractionRequest`'s own docstring gives for why it
narrows the raw event down to named fields rather than passing the event
itself."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from roottrace_worker.pipeline.retrieve.bundle import ContextBundle
from roottrace_worker.pipeline.understand.contracts import ErrorUnderstanding
from roottrace_worker.pipeline.validate.contracts import ValidationResult


class CriticUnavailable(Exception):
    """No critique was produced, for any reason — the sixth instance of
    the `XUnavailable` precedent (`TransportUnavailable` ->
    `ExtractorUnavailable` -> `ReasonerUnavailable` -> `PatcherUnavailable`
    -> `RepairerUnavailable` -> this)."""


class CritiqueRequest:
    """Everything the critic is allowed to see — `03` §S10's own list:
    "the original error and stack trace" (`understanding`), "the
    retrieved context bundle" (`bundle`), "the final diff" (`diff`, text
    only), and "the sandbox results" (`validation`)."""

    __slots__ = ("bundle", "diff", "understanding", "validation")

    def __init__(
        self,
        *,
        understanding: ErrorUnderstanding,
        bundle: ContextBundle,
        diff: str,
        validation: ValidationResult,
    ) -> None:
        self.understanding = understanding
        self.bundle = bundle
        self.diff = diff
        self.validation = validation


@runtime_checkable
class StructuredCritic(Protocol):
    """Turns a patch and its validation result into an independent review.

    Returns a plain mapping, not a `Critique` — the reply is untrusted
    until `stage.py` has recomputed `blocking` and `security_review.
    clean` for itself, same reasoning every other Gateway-backed stage in
    this package gives for not trusting a contract type straight off the
    wire."""

    async def critique(self, request: CritiqueRequest) -> Mapping[str, Any]:
        """Raise `CriticUnavailable` rather than returning a partial reply."""
        ...
