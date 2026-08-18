"""S6 — `reason`. See `stage.py` for the algorithm and `03` §S6 for the
contract it implements."""

from __future__ import annotations

from roottrace_worker.pipeline.reason.contracts import (
    BlastRadius,
    EliminatedHypothesis,
    Evidence,
    FixStrategy,
    IntroducedBy,
    ReasoningStep,
    RootCause,
    RootCauseAnalysis,
    TokenUsage,
)
from roottrace_worker.pipeline.reason.extraction_schema import ReasonReply
from roottrace_worker.pipeline.reason.gateway_reasoner import GatewayReasoner
from roottrace_worker.pipeline.reason.reasoner import (
    ReasonerUnavailable,
    ReasonRequest,
    StructuredReasoner,
)
from roottrace_worker.pipeline.reason.stage import ReasonOutcome, reason

__all__ = [
    "BlastRadius",
    "EliminatedHypothesis",
    "Evidence",
    "FixStrategy",
    "GatewayReasoner",
    "IntroducedBy",
    "ReasonOutcome",
    "ReasonReply",
    "ReasonRequest",
    "ReasonerUnavailable",
    "ReasoningStep",
    "RootCause",
    "RootCauseAnalysis",
    "StructuredReasoner",
    "TokenUsage",
    "reason",
]
