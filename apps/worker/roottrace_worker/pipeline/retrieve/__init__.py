"""S5 — `retrieve`. See `path_resolution.py` for cascade steps 3-4 (T4.2);
the strategies themselves (A, B, D, E) and ranking/budget are T4.3/T4.4."""

from __future__ import annotations

from roottrace_worker.pipeline.retrieve.path_resolution import (
    CONFIDENCE_FILENAME_AMBIGUOUS,
    CONFIDENCE_FILENAME_UNIQUE,
    CONFIDENCE_NOT_FOUND,
    CONFIDENCE_SUFFIX_MULTIPLE,
    CONFIDENCE_SUFFIX_UNIQUE,
    PathMappingResult,
    TreeResolution,
    dry_run_path_mapping,
    resolve_against_tree,
    resolve_frame_path,
    resolve_scope,
)

__all__ = [
    "CONFIDENCE_FILENAME_AMBIGUOUS",
    "CONFIDENCE_FILENAME_UNIQUE",
    "CONFIDENCE_NOT_FOUND",
    "CONFIDENCE_SUFFIX_MULTIPLE",
    "CONFIDENCE_SUFFIX_UNIQUE",
    "PathMappingResult",
    "TreeResolution",
    "dry_run_path_mapping",
    "resolve_against_tree",
    "resolve_frame_path",
    "resolve_scope",
]
