"""`03` §S9's own routing table: `failed_gate` → `strategy` +
`reroute_to_stage`, always deterministic — a model is never asked to pick
either, only to write `instruction_delta` around them (`repairer.py`'s
module docstring). `ai/prompts/repair/gate_instructions.py` (written
ahead of this ticket, alongside T5.1/T5.2's prompt-registry work) already
carries `A2` §7's gate-specific instruction text verbatim; this module is
its first real caller, pairing each instruction with the strategy name
and reroute target `03` §S9's algorithm gives it.

**G5 is the one row that reroutes to `S6`, not `S7`.** `03`: "if the fix
doesn't fix it, patching harder is futile — the diagnosis was wrong."
Every other gate re-enters `S7` with a correction; only a wrong diagnosis
sends the pipeline back to reasoning."""

from __future__ import annotations

from dataclasses import dataclass

from roottrace_worker.ai.prompts.repair.gate_instructions import GATE_INSTRUCTIONS
from roottrace_worker.pipeline.repair.contracts import RerouteStage, Strategy


@dataclass(frozen=True, slots=True)
class GateRoute:
    strategy: Strategy
    reroute_to_stage: RerouteStage
    gate_specific_instruction: str


_ROUTES: dict[str, tuple[Strategy, RerouteStage]] = {
    "G1": ("targeted_syntax_fix", "S7"),
    "G2": ("use_available_imports", "S7"),
    "G3": ("fix_compile_error", "S7"),
    "G4": ("regenerate_test_only", "S7"),
    "G5": ("reconsider_root_cause", "S6"),
    "G6": ("preserve_existing_contract", "S7"),
    "G7": ("remediate_static_findings", "S7"),
    "G8": ("remediate_security_construct", "S7"),
}


class UnroutableGateError(ValueError):
    """`failed_gate` is not one of G1-G8. `gate_instructions.py`'s own
    docstring already settled this before T7.1 existed: "an unrecognised
    gate is a bug in the caller, not a case this table should grow a
    default for." Deliberately excluded, not merely unhandled:

    - **G0** (diff-apply, host-side, before any container exists) — never
      named in `03` §S9's routing table or `A2` §7's instruction table,
      both of which start at G1.
    - **`"timeout"`** — `03` §S8 says a hard-kill "enters the repair loop
      like any other failure," but `SandboxOrchestrator._timeout_result`
      (T6.2) sets `gates=()`, so there is no way to know *which* gate was
      running when the kill fired — routing it to a specific gate's
      instruction would be a guess dressed up as a fact. Disclosed as an
      open tension with `03` §S8's own prose in `PROJECT-STATUS.md`'s
      T7.1 section, not silently resolved.
    - **`"runner_error"`** — `roottrace_sandbox_runner`'s own internal
      safety net (T6.4), never a documented gate; a defect in *our* code,
      not the patch's, and re-prompting the model cannot fix it."""


def route_for_gate(failed_gate: str) -> GateRoute:
    pair = _ROUTES.get(failed_gate)
    instruction = GATE_INSTRUCTIONS.get(failed_gate)
    if pair is None or instruction is None:
        raise UnroutableGateError(f"{failed_gate!r} has no repair route — only G1-G8 do")
    strategy, reroute_to_stage = pair
    return GateRoute(
        strategy=strategy,
        reroute_to_stage=reroute_to_stage,
        gate_specific_instruction=instruction,
    )
