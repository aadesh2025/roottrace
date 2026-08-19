"""S9 — `repair`. See `stage.py` for the algorithm and `03` §S9 for the
contract it implements."""

from __future__ import annotations

from roottrace_worker.pipeline.repair.contracts import (
    PreviousAttempt,
    RepairDecision,
    RepairExhausted,
    RerouteStage,
    Strategy,
)
from roottrace_worker.pipeline.repair.extraction_schema import RepairReply
from roottrace_worker.pipeline.repair.gateway_repairer import GatewayRepairer
from roottrace_worker.pipeline.repair.repairer import (
    RepairerUnavailable,
    RepairRequest,
    StructuredRepairer,
)
from roottrace_worker.pipeline.repair.routing import GateRoute, UnroutableGateError, route_for_gate
from roottrace_worker.pipeline.repair.stage import RepairOutcome, repair

__all__ = [
    "GateRoute",
    "GatewayRepairer",
    "PreviousAttempt",
    "RepairDecision",
    "RepairExhausted",
    "RepairOutcome",
    "RepairReply",
    "RepairRequest",
    "RepairerUnavailable",
    "RerouteStage",
    "Strategy",
    "StructuredRepairer",
    "UnroutableGateError",
    "repair",
    "route_for_gate",
]
