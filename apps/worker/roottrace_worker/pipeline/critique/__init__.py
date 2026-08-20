"""S10 — `critique`. See `stage.py` for the algorithm and `03` §S10 for
the contract it implements."""

from __future__ import annotations

from roottrace_worker.pipeline.critique.contracts import (
    Critique,
    CritiqueUnavailable,
    Dimension,
    Finding,
    FindingEvidence,
    SecurityReview,
    Severity,
    TestQuality,
    TokenUsage,
    Verdict,
)
from roottrace_worker.pipeline.critique.critic import (
    CriticUnavailable,
    CritiqueRequest,
    StructuredCritic,
)
from roottrace_worker.pipeline.critique.extraction_schema import CritiqueReply
from roottrace_worker.pipeline.critique.gateway_critic import GatewayCritic, split_critique_prompt
from roottrace_worker.pipeline.critique.stage import CritiqueOutcome, critique

__all__ = [
    "CriticUnavailable",
    "Critique",
    "CritiqueOutcome",
    "CritiqueReply",
    "CritiqueRequest",
    "CritiqueUnavailable",
    "Dimension",
    "Finding",
    "FindingEvidence",
    "GatewayCritic",
    "SecurityReview",
    "Severity",
    "StructuredCritic",
    "TestQuality",
    "TokenUsage",
    "Verdict",
    "critique",
    "split_critique_prompt",
]
