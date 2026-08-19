"""The seam where S9's repair-routing LLM call plugs in (`03` §S9, `06`
§8.1's cost table: "S9 repair routing | fast | 2k / 0.5k | $0.002").

**Unlike S6/S7, S9 has a real deterministic floor — `routing.py`'s
`strategy`/`reroute_to_stage` are always known without a model.** The
model's only job is `instruction_delta`: a case-specific paraphrase of
the deterministic `gate_specific_instruction`, informed by the actual
sandbox failure detail, the previous attempts, and the original
diagnosis. A paraphrase has an honest fallback its source material — the
deterministic instruction text itself is already a complete, usable
`instruction_delta`, just less tailored to this one failure than a
model-authored version would be. `stage.py` uses exactly that fallback on
`RepairerUnavailable`, the same "continue, never terminal" precedent `03`
§S4 sets for its own fast-tier enhancement over a deterministic pre-parse
that already stands alone."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from roottrace_worker.pipeline.patch.contracts import Patch
from roottrace_worker.pipeline.reason.contracts import RootCauseAnalysis
from roottrace_worker.pipeline.repair.contracts import PreviousAttempt
from roottrace_worker.pipeline.understand.contracts import ErrorUnderstanding


class RepairerUnavailable(Exception):
    """No repair-routing reply was produced, for any reason — the fifth
    instance of the `XUnavailable` precedent (`TransportUnavailable` ->
    `ExtractorUnavailable` -> `ReasonerUnavailable` -> `PatcherUnavailable`
    -> this)."""


class RepairRequest:
    """Everything the repairer is allowed to see — `03` §S9's own
    `repair_context`: the original `ErrorUnderstanding` (S4), the original
    `RootCause` (S6, carried inside `RootCauseAnalysis`), the failed
    `Patch` (S7), the failed gate's own detail (the closest artefact this
    stage has to "the full sandbox transcript" — `ValidationResult`
    itself carries only byte counts and a `transcript_url` this ticket
    does not fetch; see `PROJECT-STATUS.md`'s T7.1 section for why that
    scoping is deliberate), and every previous attempt."""

    __slots__ = (
        "failed_gate",
        "failure_detail",
        "gate_specific_instruction",
        "patch",
        "previous_attempts",
        "root_cause",
        "understanding",
    )

    def __init__(
        self,
        *,
        understanding: ErrorUnderstanding,
        root_cause: RootCauseAnalysis,
        patch: Patch,
        failed_gate: str,
        gate_specific_instruction: str,
        failure_detail: Mapping[str, Any],
        previous_attempts: tuple[PreviousAttempt, ...],
    ) -> None:
        self.understanding = understanding
        self.root_cause = root_cause
        self.patch = patch
        self.failed_gate = failed_gate
        self.gate_specific_instruction = gate_specific_instruction
        self.failure_detail = failure_detail
        self.previous_attempts = previous_attempts


@runtime_checkable
class StructuredRepairer(Protocol):
    """Turns a validation failure into a case-specific `instruction_delta`.

    Returns a plain mapping, not a `RepairDecision` — `strategy`/
    `reroute_to_stage` are never part of what this seam produces at all
    (`routing.py` already knows them); only `instruction_delta` travels
    back, and even that is untrusted text until `stage.py` decides whether
    to use it."""

    async def repair(self, request: RepairRequest) -> Mapping[str, Any]:
        """Raise `RepairerUnavailable` rather than returning a partial reply."""
        ...
