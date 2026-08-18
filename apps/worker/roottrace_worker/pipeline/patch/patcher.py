"""The seam where S7's diff-generation call plugs in (`03` §S7) — same shape
as `reason.reasoner.StructuredReasoner`: a `Protocol`, one exception, no
deterministic fallback (there is no floor to fall back to for "write a
diff" any more than there was for "diagnose a root cause")."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, Protocol, runtime_checkable

from roottrace_worker.pipeline.reason.contracts import RootCauseAnalysis
from roottrace_worker.pipeline.retrieve.bundle import ContextBundle

#: `17` GLOSSARY's registered failure codes a patcher can terminate with.
#: `RT-AI-0005`/`RT-AI-0006` are S7's own two (see `validate.py`'s module
#: docstring for which deterministic check falls into which); `RT-AI-0001`
#: is the generic "gateway exhausted every provider" code every stage that
#: calls through `LLMGateway` can hit, unrelated to S7's own semantic
#: checks — a provider outage is not a scope violation or a bad diff.
PatchErrorCode = Literal["RT-AI-0001", "RT-AI-0005", "RT-AI-0006"]


class PatcherUnavailable(Exception):
    """No patch was produced, for any reason — the fourth instance of the
    `XUnavailable` precedent (`TransportUnavailable` -> `ExtractorUnavailable`
    -> `ReasonerUnavailable` -> this). Carries `error_code`: unlike
    `ReasonerUnavailable`, S7 has two registered codes (`RT-AI-0005` scope
    violation, `RT-AI-0006` non-applying diff) rather than one, and the
    difference is meaningful for observability even though the stage's
    response to either is identical: terminal `failed` (`03` §6 line 1463 —
    no `insufficient_context` equivalent exists for S7, since that is an
    S5/S6 concept about the *evidence*, not about whether a diff was
    writable)."""

    def __init__(self, message: str, *, error_code: PatchErrorCode) -> None:
        super().__init__(message)
        self.error_code: PatchErrorCode = error_code


class PatchRequest:
    """Everything the patcher is allowed to see. `analysis` carries S6's
    `fix_strategy` (what to change and why); `bundle` carries the actual
    retrieved content the diff must apply against."""

    __slots__ = ("analysis", "bundle")

    def __init__(self, *, bundle: ContextBundle, analysis: RootCauseAnalysis) -> None:
        self.bundle = bundle
        self.analysis = analysis


@runtime_checkable
class StructuredPatcher(Protocol):
    """Turns a root-cause analysis into the model-authored diff.

    Returns a plain mapping, not a `Patch` — the reply is untrusted until
    `validate.py` has checked every `03` §S7 constraint, same reasoning
    `StructuredReasoner` documents for the identical design choice."""

    async def patch(self, request: PatchRequest) -> Mapping[str, Any]:
        """Raise `PatcherUnavailable` rather than returning a partial reply."""
        ...
