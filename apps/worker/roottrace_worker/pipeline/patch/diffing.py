"""In-memory diff parsing and application (`03` §S7: "We apply it in-memory
with `unidiff` before accepting"). `unidiff` parses; it has no built-in
"apply to a string" — that half is ours, walking each hunk's lines against
the actual retrieved content rather than trusting the model's line numbers.

**A `BundleFile.content` is a window, not the whole file** (`03` §S5's
frame-direct fetch, `line_range`-bounded). A hunk's `@@ -N,M +N,M @@` header
uses real, whole-file line numbers — the same numbers the model was shown
via `_file_block`'s `lines=lo-hi` attribute (`gateway_patcher.py`) — so a
hunk is only checkable at all when its full source range falls inside the
window that was actually retrieved. A hunk reaching outside that window
cannot be verified against real content and is treated as a failure to
apply cleanly, not silently accepted on faith."""

from __future__ import annotations

from dataclasses import dataclass

import unidiff
from unidiff.errors import UnidiffParseError

from roottrace_worker.pipeline.retrieve.bundle import ContextBundle


@dataclass(frozen=True, slots=True)
class FileStat:
    repo_path: str
    additions: int
    deletions: int
    hunks: int
    is_added_file: bool
    is_removed_file: bool


@dataclass(frozen=True, slots=True)
class DiffParseResult:
    patch_set: unidiff.PatchSet | None
    file_stats: tuple[FileStat, ...]
    error: str | None

    @property
    def ok(self) -> bool:
        return self.patch_set is not None


def parse_diff(diff_text: str) -> DiffParseResult:
    try:
        patch_set = unidiff.PatchSet(diff_text)
    except (UnidiffParseError, ValueError) as exc:
        return DiffParseResult(patch_set=None, file_stats=(), error=str(exc))

    if not patch_set:
        # `unidiff` does not raise on text with no recognisable `---`/`+++`
        # headers at all — it silently parses to zero files. A "diff" that
        # touches nothing is not a valid patch either.
        return DiffParseResult(patch_set=None, file_stats=(), error="diff contains no file changes")

    stats = tuple(
        FileStat(
            repo_path=pf.path,
            additions=pf.added,
            deletions=pf.removed,
            hunks=len(pf),
            is_added_file=pf.is_added_file,
            is_removed_file=pf.is_removed_file,
        )
        for pf in patch_set
    )
    return DiffParseResult(patch_set=patch_set, file_stats=stats, error=None)


@dataclass(frozen=True, slots=True)
class ApplyResult:
    applies_cleanly: bool
    failure_reason: str | None = None


def apply_diff_to_bundle(patch_set: unidiff.PatchSet, *, bundle: ContextBundle) -> ApplyResult:
    """Every hunk in every file must apply against real retrieved content, or
    against nothing at all for a genuinely new file. The first failure wins —
    `03` §S7 has no notion of "partially applies".

    Two retrieved-content sources, not one: `bundle.files` (strategy A/B/D
    windows, `line_range`-bounded) and `bundle.tests.found` (strategy E,
    `03` §S5 — whole-file fetches, so always windowed from line 1). A patch
    touching an *existing* test file only has content to check against in
    the second source."""
    files_by_path = {f.repo_path: f for f in bundle.files}
    tests_by_path = {t.repo_path: t.content for t in bundle.tests.found}

    for pf in patch_set:
        if pf.is_added_file:
            result = _apply_new_file(pf)
        else:
            bundle_file = files_by_path.get(pf.path)
            if bundle_file is not None:
                result = _apply_against_window(
                    pf, content=bundle_file.content, window_start=bundle_file.line_range[0]
                )
            elif pf.path in tests_by_path:
                result = _apply_against_window(pf, content=tests_by_path[pf.path], window_start=1)
            else:
                return ApplyResult(
                    applies_cleanly=False,
                    failure_reason=f"{pf.path}: not present in the retrieved bundle, "
                    "cannot verify the diff applies",
                )
        if not result.applies_cleanly:
            return result

    return ApplyResult(applies_cleanly=True)


def _apply_new_file(pf: unidiff.PatchedFile) -> ApplyResult:
    for hunk in pf:
        if hunk.source_length != 0:
            return ApplyResult(
                applies_cleanly=False,
                failure_reason=f"{pf.path}: marked as a new file but a hunk declares "
                "existing source content",
            )
    return ApplyResult(applies_cleanly=True)


@dataclass(frozen=True, slots=True)
class AppliedDiff:
    """G0 (`03` §S8, `07` §6): "patched_files = apply_unified_diff(bundle.files,
    patch.diff)". Distinct from `apply_diff_to_bundle` above — that function
    checks a diff against a *retrieval window* (T5.4, before a patch is even
    trusted); this one applies a diff against *full file content* (S8, once
    a patch already has real content to modify) and actually produces the
    patched text, which nothing before S8 needed to do."""

    files_patched: dict[str, str] | None
    failure_reason: str | None

    @property
    def ok(self) -> bool:
        return self.files_patched is not None


def apply_diff_to_files(diff_text: str, files_original: dict[str, str]) -> AppliedDiff:
    """Files untouched by the diff carry over into the result unchanged —
    `07` §6's G0 is about the *changed* files applying cleanly, not about
    every file in the bundle being mentioned in the diff."""
    parsed = parse_diff(diff_text)
    if not parsed.ok:
        return AppliedDiff(
            files_patched=None, failure_reason=f"diff failed to parse: {parsed.error}"
        )

    assert parsed.patch_set is not None  # noqa: S101 - `parsed.ok` guarantees this
    result = dict(files_original)

    for pf in parsed.patch_set:
        if pf.is_removed_file:
            result.pop(pf.path, None)
            continue

        original = "" if pf.is_added_file else files_original.get(pf.path)
        if original is None:
            return AppliedDiff(
                files_patched=None, failure_reason=f"{pf.path}: not present in files_original"
            )

        patched = _apply_full_file(pf, content=original)
        if patched is None:
            return AppliedDiff(
                files_patched=None, failure_reason=f"{pf.path}: a hunk does not match the file"
            )
        result[pf.path] = patched

    return AppliedDiff(files_patched=result, failure_reason=None)


def _apply_full_file(pf: unidiff.PatchedFile, *, content: str) -> str | None:
    """Same hunk-walking logic `_apply_against_window` uses, generalised to
    a full file (`window_start` is always `1`, since there is no retrieval
    window here) and building the patched output rather than only checking
    it — `None` on any mismatch, same "first failure wins" rule."""
    lines = content.splitlines(keepends=True)
    output: list[str] = []
    cursor = 0  # 0-indexed position in `lines` already copied into `output`

    for hunk in pf:
        index = hunk.source_start - 1 if hunk.source_length > 0 else hunk.source_start
        if index < cursor or index > len(lines):
            return None
        output.extend(lines[cursor:index])
        cursor = index

        for line in hunk:
            if line.is_added:
                output.append(line.value)
                continue
            if cursor >= len(lines) or lines[cursor] != line.value:
                return None
            if line.is_context:
                output.append(line.value)
            cursor += 1

    output.extend(lines[cursor:])
    return "".join(output)


def _apply_against_window(
    pf: unidiff.PatchedFile, *, content: str, window_start: int
) -> ApplyResult:
    window_lines = content.splitlines(keepends=True)
    window_len = len(window_lines)

    for hunk in pf:
        if hunk.source_length == 0:
            continue  # pure insertion within an already-retrieved file
        index = hunk.source_start - window_start
        if index < 0 or index + hunk.source_length > window_len:
            return ApplyResult(
                applies_cleanly=False,
                failure_reason=f"{pf.path}: hunk at line {hunk.source_start} falls outside "
                "the retrieved content",
            )
        cursor = index
        for line in hunk:
            if line.is_added:
                continue
            if window_lines[cursor] != line.value:
                return ApplyResult(
                    applies_cleanly=False,
                    failure_reason=f"{pf.path}:{cursor + window_start} does not match the "
                    "retrieved content",
                )
            cursor += 1

    return ApplyResult(applies_cleanly=True)
