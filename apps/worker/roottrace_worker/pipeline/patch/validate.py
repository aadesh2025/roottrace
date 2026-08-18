"""Deterministic post-validation for S7 (`03` §S7's "Constraints enforced on
the output" table). Every check here is mechanical — no model judgment, no
retrieved-content ambiguity, exactly the "Deterministic" enforcement column
`03` names for each row.

**Two failure buckets, matching `17` GLOSSARY's two registered codes.**
`RT-AI-0005` ("Patch scope violation after retry") covers everything about
*which files were touched*: a forbidden path, a file outside
`files_to_modify` plus the declared regression-test path, a file named in
`fix_strategy.must_not_modify`, or an existing test deleted. `RT-AI-0006`
("Diff does not apply after retry") covers everything about *whether the
patch is usable as delivered*: the diff fails to parse, a hunk does not
match the retrieved content, or a regression test `03` required is missing
— `03` gives this constraint its own "Deterministic" row but no third
registered code, and a missing-but-required test is closer in kind to "the
patch isn't acceptable as given" than to a scope/injection signal, so it is
bucketed here rather than invented a third code `17` does not list."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from roottrace_worker.pipeline.patch.diffing import (
    DiffParseResult,
    apply_diff_to_bundle,
    parse_diff,
)
from roottrace_worker.pipeline.patch.extraction_schema import PatchReply
from roottrace_worker.pipeline.reason.contracts import RootCauseAnalysis
from roottrace_worker.pipeline.retrieve.bundle import ContextBundle

#: `A2` §5's hard constraint 2, verbatim: "Never modify: .github/**,
#: Dockerfile, docker-compose*, *.lock, CI configuration."
_FORBIDDEN_PATH_GLOBS = (
    ".github/*",
    "Dockerfile",
    "Dockerfile.*",
    "docker-compose*",
    "*.lock",
)

#: `03` §S7's constraint table: "Must not touch dependency manifests unless
#: explicitly required | Flagged for human review if it does." A soft
#: signal, not a hard failure — surfaced via `PatchValidation.warnings`.
_DEPENDENCY_MANIFEST_GLOBS = (
    "pyproject.toml",
    "requirements*.txt",
    "package.json",
    "Pipfile",
    "Cargo.toml",
    "go.mod",
)

#: `03` §S7's scope-warning heuristic ceiling.
_MAX_CHANGED_LINES = 60
_MAX_HUNKS = 5


def _matches_any(path: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in globs)


def is_forbidden_path(path: str) -> bool:
    return _matches_any(path, _FORBIDDEN_PATH_GLOBS)


def is_dependency_manifest(path: str) -> bool:
    return _matches_any(path, _DEPENDENCY_MANIFEST_GLOBS)


@dataclass(frozen=True, slots=True)
class ScopeViolation:
    reason: str


@dataclass(frozen=True, slots=True)
class ApplicabilityFailure:
    reason: str


@dataclass(frozen=True, slots=True)
class PatchValidation:
    parsed: DiffParseResult
    scope_violation: ScopeViolation | None
    applicability_failure: ApplicabilityFailure | None
    scope_warning: str | None
    manifest_warning: str | None

    @property
    def ok(self) -> bool:
        return self.scope_violation is None and self.applicability_failure is None


def _allowed_paths(reply: PatchReply, *, analysis: RootCauseAnalysis) -> set[str]:
    allowed = set(analysis.fix_strategy.files_to_modify)
    if reply.regression_test is not None and reply.regression_test.repo_path:
        allowed.add(reply.regression_test.repo_path)
    return allowed


def _check_scope(
    parsed: DiffParseResult,
    *,
    reply: PatchReply,
    analysis: RootCauseAnalysis,
    bundle: ContextBundle,
) -> ScopeViolation | None:
    assert parsed.patch_set is not None  # noqa: S101 - only called after a successful parse
    allowed = _allowed_paths(reply, analysis=analysis)
    must_not_modify = set(analysis.fix_strategy.must_not_modify)
    test_paths = {t.repo_path for t in bundle.tests.found}

    for stat in parsed.file_stats:
        if is_forbidden_path(stat.repo_path):
            return ScopeViolation(f"{stat.repo_path} is a forbidden path (CI/dependency lockfile)")
        if stat.repo_path in must_not_modify:
            return ScopeViolation(
                f"{stat.repo_path} is explicitly listed in fix_strategy.must_not_modify"
            )
        if stat.repo_path not in allowed:
            return ScopeViolation(
                f"{stat.repo_path} is outside fix_strategy.files_to_modify and is not the "
                "declared regression test"
            )
        if stat.is_removed_file and stat.repo_path in test_paths:
            return ScopeViolation(f"{stat.repo_path} is an existing test and the diff deletes it")

    for pf in parsed.patch_set:
        if pf.path not in test_paths:
            continue
        removed_tests = {
            line.value.strip()[len("def ") :].split("(")[0]
            for hunk in pf
            for line in hunk
            if line.is_removed and line.value.strip().startswith("def test_")
        }
        added_tests = {
            line.value.strip()[len("def ") :].split("(")[0]
            for hunk in pf
            for line in hunk
            if line.is_added and line.value.strip().startswith("def test_")
        }
        deleted = removed_tests - added_tests
        if deleted:
            return ScopeViolation(
                f"{pf.path}: existing test(s) removed without replacement: {sorted(deleted)}"
            )

    return None


def _scope_warning(parsed: DiffParseResult) -> str | None:
    total_changed = sum(stat.additions + stat.deletions for stat in parsed.file_stats)
    total_hunks = sum(stat.hunks for stat in parsed.file_stats)
    if total_changed > _MAX_CHANGED_LINES or total_hunks > _MAX_HUNKS:
        return (
            f"{total_changed} changed lines across {total_hunks} hunks exceeds the "
            f"{_MAX_CHANGED_LINES}-line / {_MAX_HUNKS}-hunk scope heuristic"
        )
    return None


def _manifest_warning(parsed: DiffParseResult) -> str | None:
    touched = sorted(
        {stat.repo_path for stat in parsed.file_stats if is_dependency_manifest(stat.repo_path)}
    )
    if touched:
        return f"dependency manifest(s) touched, flagged for human review: {touched}"
    return None


def validate_patch(
    reply: PatchReply, *, bundle: ContextBundle, analysis: RootCauseAnalysis
) -> PatchValidation:
    parsed = parse_diff(reply.diff)
    if not parsed.ok:
        return PatchValidation(
            parsed=parsed,
            scope_violation=None,
            applicability_failure=ApplicabilityFailure(f"diff failed to parse: {parsed.error}"),
            scope_warning=None,
            manifest_warning=None,
        )

    scope_violation = _check_scope(parsed, reply=reply, analysis=analysis, bundle=bundle)
    if scope_violation is not None:
        return PatchValidation(
            parsed=parsed,
            scope_violation=scope_violation,
            applicability_failure=None,
            scope_warning=None,
            manifest_warning=None,
        )

    assert parsed.patch_set is not None  # noqa: S101 - `parsed.ok` guarantees this
    apply_result = apply_diff_to_bundle(parsed.patch_set, bundle=bundle)
    if not apply_result.applies_cleanly:
        return PatchValidation(
            parsed=parsed,
            scope_violation=None,
            applicability_failure=ApplicabilityFailure(
                apply_result.failure_reason or "diff does not apply"
            ),
            scope_warning=None,
            manifest_warning=None,
        )

    if analysis.fix_strategy.regression_test_needed and reply.regression_test is None:
        return PatchValidation(
            parsed=parsed,
            scope_violation=None,
            applicability_failure=ApplicabilityFailure(
                "fix_strategy.regression_test_needed is true but no regression_test was produced"
            ),
            scope_warning=None,
            manifest_warning=None,
        )

    return PatchValidation(
        parsed=parsed,
        scope_violation=None,
        applicability_failure=None,
        scope_warning=_scope_warning(parsed),
        manifest_warning=_manifest_warning(parsed),
    )
