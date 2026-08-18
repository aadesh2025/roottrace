"""S7 — `patch`. See `stage.py` for the algorithm and `03` §S7 for the
contract it implements."""

from __future__ import annotations

from roottrace_worker.pipeline.patch.contracts import (
    Alternative,
    FileChange,
    Patch,
    RegressionTest,
    RiskAssessment,
    TokenUsage,
)
from roottrace_worker.pipeline.patch.extraction_schema import PatchReply
from roottrace_worker.pipeline.patch.gateway_patcher import GatewayPatcher
from roottrace_worker.pipeline.patch.patcher import (
    PatchErrorCode,
    PatcherUnavailable,
    PatchRequest,
    StructuredPatcher,
)
from roottrace_worker.pipeline.patch.stage import patch

__all__ = [
    "Alternative",
    "FileChange",
    "GatewayPatcher",
    "Patch",
    "PatchErrorCode",
    "PatchReply",
    "PatchRequest",
    "PatcherUnavailable",
    "RegressionTest",
    "RiskAssessment",
    "StructuredPatcher",
    "TokenUsage",
    "patch",
]
