"""S4 — `understand`. See `stage.py` for the algorithm and `03` §S4 for the
contract it implements."""

from __future__ import annotations

from roottrace_worker.pipeline.understand.contracts import (
    EntryPoint,
    ErrorUnderstanding,
    ExceptionFamily,
    ExceptionInfo,
    FailurePoint,
    Flag,
    Frame,
    Hypothesis,
    RetrievalPlan,
)
from roottrace_worker.pipeline.understand.extractor import (
    ExtractionRequest,
    ExtractorUnavailable,
    StructuredExtractor,
    UnavailableExtractor,
)
from roottrace_worker.pipeline.understand.frames import PathMapping
from roottrace_worker.pipeline.understand.stage import (
    UnderstandOutcome,
    preparse,
    understand,
)

__all__ = [
    "EntryPoint",
    "ErrorUnderstanding",
    "ExceptionFamily",
    "ExceptionInfo",
    "ExtractionRequest",
    "ExtractorUnavailable",
    "FailurePoint",
    "Flag",
    "Frame",
    "Hypothesis",
    "PathMapping",
    "RetrievalPlan",
    "StructuredExtractor",
    "UnavailableExtractor",
    "UnderstandOutcome",
    "preparse",
    "understand",
]
