"""`routing.py` (T7.1) — `03` §S9's deterministic gate → strategy /
reroute_to_stage table, pure logic, no model or container involved."""

from __future__ import annotations

import pytest

from roottrace_worker.pipeline.repair.routing import UnroutableGateError, route_for_gate

pytestmark = pytest.mark.unit

_ALL_GATES = ("G1", "G2", "G3", "G4", "G5", "G6", "G7", "G8")


@pytest.mark.parametrize("gate", _ALL_GATES)
def test_every_documented_gate_routes_to_something(gate: str) -> None:
    route = route_for_gate(gate)
    assert route.strategy
    assert route.reroute_to_stage in ("S6", "S7")
    assert route.gate_specific_instruction


def test_g5_is_the_only_gate_that_reroutes_to_reasoning() -> None:
    """`03` §S9: "if the fix doesn't fix it, patching harder is futile —
    the diagnosis was wrong." Every other gate re-enters S7."""
    reroutes = {gate: route_for_gate(gate).reroute_to_stage for gate in _ALL_GATES}
    assert reroutes["G5"] == "S6"
    assert all(stage == "S7" for gate, stage in reroutes.items() if gate != "G5")


def test_every_route_has_a_distinct_strategy_name() -> None:
    strategies = [route_for_gate(gate).strategy for gate in _ALL_GATES]
    assert len(set(strategies)) == len(strategies)


@pytest.mark.parametrize("gate", ["G0", "timeout", "runner_error", "G99", ""])
def test_an_undocumented_gate_is_not_routed(gate: str) -> None:
    """`gate_instructions.py`'s own docstring: "an unrecognised gate is a
    bug in the caller, not a case this table should grow a default for."
    G0/`"timeout"`/`"runner_error"` are real values `failed_gate` can take
    (`pipeline/validate/gates.py`, `SandboxOrchestrator._timeout_result`,
    `roottrace_sandbox_runner`'s own error result) but none of them is a
    gate `03` §S9's routing table or `A2` §7's instruction table names —
    see `routing.py`'s `UnroutableGateError` docstring for why each is
    excluded rather than guessed at."""
    with pytest.raises(UnroutableGateError):
        route_for_gate(gate)
