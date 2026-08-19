"""S9 `repair` (`03` §S9) — T7.1.

`03`'s algorithm, in order: check attempt exhaustion first (a terminal
decision needs no routing at all), then route deterministically by
`failed_gate` (`routing.py`), then ask a model for a case-specific
`instruction_delta` around the deterministic instruction text — falling
back to that deterministic text, never terminating, if the model call
fails (`repairer.py`'s module docstring has the reasoning, mirroring `03`
§S4's own precedent for a fast-tier enhancement with a real floor).

The stage is a function of its arguments, same rule every other stage in
this package follows (`pipeline/__init__.py`): it writes no
`pipeline_steps` row, decides no persistence, and does not itself fetch
the sandbox transcript from object storage — `ValidationResult` carries
only byte counts and a `transcript_url` (`04`'s `validation_runs` table),
and fetching that blob is an orchestration concern (T8.2), same as
`patch.stage.patch`'s `patch_id`/`base_commit` being caller-supplied
rather than minted here. What this stage reads instead is the failed
gate's own `detail` — already the literal, un-summarised sandbox output
for *that* gate (`stderr_tail`, `exception_type`, `newly_failing`, ...,
per whichever gate actually failed), which is the closest artefact to
"the full sandbox transcript" available without adding an object-storage
fetch no ticket before this one has built."""

from __future__ import annotations

from roottrace_worker.pipeline.patch.contracts import Patch
from roottrace_worker.pipeline.reason.contracts import RootCauseAnalysis
from roottrace_worker.pipeline.repair.contracts import (
    PreviousAttempt,
    RepairDecision,
    RepairExhausted,
)
from roottrace_worker.pipeline.repair.repairer import (
    RepairerUnavailable,
    RepairRequest,
    StructuredRepairer,
)
from roottrace_worker.pipeline.repair.routing import route_for_gate
from roottrace_worker.pipeline.understand.contracts import ErrorUnderstanding
from roottrace_worker.pipeline.validate.contracts import GateResult, ValidationResult


class RepairOutcome:
    """`RepairDecision` on a re-enterable failure, `RepairExhausted` on the
    terminal case — same exactly-one-of wrapper shape `reason.stage.
    ReasonOutcome` establishes, for the same reason: a bare union return
    forces every caller to re-derive which shape it got instead of asking."""

    __slots__ = ("decision", "exhausted")

    def __init__(
        self,
        *,
        decision: RepairDecision | None = None,
        exhausted: RepairExhausted | None = None,
    ) -> None:
        if (decision is None) == (exhausted is None):
            raise ValueError("exactly one of decision or exhausted must be set")
        self.decision = decision
        self.exhausted = exhausted


def _find_gate_result(validation: ValidationResult) -> GateResult | None:
    """`None`, not a raise, when nothing matches — `03` §S9's algorithm
    checks attempt exhaustion *before* it ever asks what the failure was
    (see `repair()` below), and a non-gate failure legitimately has no
    entry to find at all: `SandboxOrchestrator._timeout_result` (T6.2) and
    the sandbox-runner's own error result (T6.4) both report `gates=()`
    for `"timeout"`/`"runner_error"`. A real G1-G8 failure always does
    have a matching entry (T6.4's fail-fast design appends the failing
    gate's own result before returning), so `None` here in practice means
    "this was never a gate failure to begin with," not "the caller's data
    is broken.\""""
    for gate in validation.gates:
        if gate.gate == validation.failed_gate:
            return gate
    return None


def _failure_reason(validation: ValidationResult, gate: GateResult | None) -> str:
    """A short, human-legible reason for this attempt's history entry.
    Reused verbatim when the gate's own `detail` already states one — most
    of T6.4's gates do (`G4`'s "test PASSED on unpatched code", `G5`'s
    "diagnosis may be wrong", ...) — rather than inventing a second
    summary of a fact already on hand. `G6`/`G8` are the two gates whose
    `detail` never carries a `"reason"` key at all (`gates.py`'s own
    shape), so they get a purpose-built one from the field they do carry.
    A `None` gate (no matching entry — see `_find_gate_result`) falls back
    to naming the raw `failed_gate` value itself, the only fact available."""
    if gate is None:
        return f"{validation.failed_gate} (no gate detail available)"
    reason = gate.detail.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    if gate.gate == "G6":
        newly_failing = gate.detail.get("newly_failing")
        if isinstance(newly_failing, list) and newly_failing:
            return f"newly failing: {', '.join(str(t) for t in newly_failing)}"
    if gate.gate == "G8":
        findings = gate.detail.get("findings")
        if isinstance(findings, list) and findings:
            return f"{len(findings)} dangerous construct(s) found"
    return f"{gate.gate} failed"


async def repair(
    validation: ValidationResult,
    *,
    understanding: ErrorUnderstanding,
    root_cause: RootCauseAnalysis,
    patch: Patch,
    previous_attempts: tuple[PreviousAttempt, ...],
    attempt: int,
    repair_id: str,
    repairer: StructuredRepairer | None = None,
    max_attempts: int = 3,
) -> RepairOutcome:
    """Run S9 over one validation failure. `attempt` and `repair_id` are
    caller-supplied, same precedent `patch.stage.patch`'s `patch_id`/
    `base_commit` sets — minting identifiers and tracking how many
    attempts have already happened is orchestration state, not something
    a pure stage function keeps for itself."""
    if validation.passed:
        raise ValueError("repair() is only for a failed ValidationResult")
    if validation.failed_gate is None:
        raise ValueError("validation.failed_gate is required for a failed ValidationResult")

    gate_result = _find_gate_result(validation)
    this_attempt = PreviousAttempt(
        attempt=attempt,
        failed_gate=validation.failed_gate,
        reason=_failure_reason(validation, gate_result),
    )
    attempts_summary = (*previous_attempts, this_attempt)

    if attempt >= max_attempts:
        return RepairOutcome(
            exhausted=RepairExhausted(
                repair_id=repair_id,
                attempt=attempt,
                failed_gate=validation.failed_gate,
                attempts_summary=attempts_summary,
            )
        )

    # Raises `UnroutableGateError` for anything other than G1-G8 — a
    # non-gate failure (`"timeout"`, `"runner_error"`) or an exhausted
    # loop never reaches this line (both return above), so this is the
    # first point a genuinely un-repairable gate can still be caught.
    route = route_for_gate(validation.failed_gate)

    instruction_delta = route.gate_specific_instruction
    if repairer is not None:
        try:
            reply = await repairer.repair(
                RepairRequest(
                    understanding=understanding,
                    root_cause=root_cause,
                    patch=patch,
                    failed_gate=validation.failed_gate,
                    gate_specific_instruction=route.gate_specific_instruction,
                    failure_detail=gate_result.detail if gate_result is not None else {},
                    previous_attempts=previous_attempts,
                )
            )
        except RepairerUnavailable:
            reply = None  # `03` §S4's precedent: never terminal — the deterministic text stands.

        if reply is not None:
            delta = reply.get("instruction_delta")
            if isinstance(delta, str) and delta.strip():
                instruction_delta = delta

    return RepairOutcome(
        decision=RepairDecision(
            repair_id=repair_id,
            attempt=attempt,
            failed_gate=validation.failed_gate,
            strategy=route.strategy,
            reroute_to_stage=route.reroute_to_stage,
            instruction_delta=instruction_delta,
            previous_attempts_summary=attempts_summary,
        )
    )
