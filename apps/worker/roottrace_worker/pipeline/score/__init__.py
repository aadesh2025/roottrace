"""S11 — `score`. See `stage.py` for the algorithm and `03` §S11 for the
contract it implements."""

from __future__ import annotations

from roottrace_worker.pipeline.score.contracts import (
    Band,
    Breakdown,
    ComponentBreakdown,
    ComponentName,
    ConfidenceScore,
    GateName,
    PublishMode,
)
from roottrace_worker.pipeline.score.stage import score

__all__ = [
    "Band",
    "Breakdown",
    "ComponentBreakdown",
    "ComponentName",
    "ConfidenceScore",
    "GateName",
    "PublishMode",
    "score",
]
