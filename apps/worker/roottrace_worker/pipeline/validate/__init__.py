"""S8 — `validate` (sandbox). See `orchestrator.py` for the algorithm and
`docs/07` for the contract it implements."""

from __future__ import annotations

from roottrace_worker.pipeline.validate.contracts import (
    GateResult,
    Manifest,
    RegressionTestRef,
    ResourceUsage,
    SandboxInput,
    SignalsForScoring,
    Transcript,
    ValidationResult,
)
from roottrace_worker.pipeline.validate.orchestrator import (
    ResultExtractionError,
    SandboxOrchestrator,
    SandboxReaper,
    SandboxTimeoutError,
)
from roottrace_worker.pipeline.validate.transcript import sanitize, truncate_middle

__all__ = [
    "GateResult",
    "Manifest",
    "RegressionTestRef",
    "ResourceUsage",
    "ResultExtractionError",
    "SandboxInput",
    "SandboxOrchestrator",
    "SandboxReaper",
    "SandboxTimeoutError",
    "SignalsForScoring",
    "Transcript",
    "ValidationResult",
    "sanitize",
    "truncate_middle",
]
