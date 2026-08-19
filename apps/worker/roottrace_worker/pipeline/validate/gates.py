"""G0 and G1 (`03` §S8, `07` §6) — the two gates that run *before* a
container ever exists. Both are in-process and cheap (`07`: "~5 ms" / "~50
ms"); catching a bad patch here instead of after paying for a container
start is the entire reason `03` §S8 splits G0/G1 out from G2-G8, which run
inside the sandbox and are implemented in
`apps/sandbox-runner/roottrace_sandbox_runner/gates.py` instead — a
different deployable, so a different module, not this one."""

from __future__ import annotations

import ast
import time

from roottrace_worker.pipeline.patch.diffing import apply_diff_to_files
from roottrace_worker.pipeline.validate.contracts import GateResult


def check_diff_applies(
    diff: str, files_original: dict[str, str]
) -> tuple[GateResult, dict[str, str] | None]:
    """G0: "patched_files = apply_unified_diff(bundle.files, patch.diff)."
    Fails if any hunk doesn't apply." Returns the gate result and, on
    success, the patched file content the rest of the pipeline (G1, then
    `SandboxInput.files_patched`) needs — this is the one gate that
    produces a value beyond pass/fail, since nothing before S8 had reason
    to materialise patched text (T5.4 only ever checked applicability)."""
    started = time.monotonic()
    applied = apply_diff_to_files(diff, files_original)
    duration_ms = int((time.monotonic() - started) * 1000)

    if not applied.ok:
        return (
            GateResult(
                gate="G0",
                passed=False,
                duration_ms=duration_ms,
                detail={"reason": applied.failure_reason or "diff does not apply"},
            ),
            None,
        )

    files_patched = applied.files_patched or {}
    return (
        GateResult(
            gate="G0",
            passed=True,
            duration_ms=duration_ms,
            detail={"files_changed": len(files_patched)},
        ),
        files_patched,
    )


#: `07` §6 G1 scopes this to Python for V1 (TS/JS/Go are V2/V5). Extensions
#: outside this set are not source files this gate has an opinion about —
#: a new test fixture's `.json`/`.txt` payload, for instance — and are
#: silently not checked, not silently failed.
_PYTHON_EXTENSIONS = (".py",)


def check_syntax(files_patched: dict[str, str]) -> GateResult:
    """G1: "Tree-sitter parse of every changed file. Any `ERROR` node fails
    the gate." Uses `ast.parse`, not Tree-sitter, for the same reason
    `pipeline/retrieve/ast_index.py` does (T4.3's own implementation note):
    V1's corpus, fixture repository, and sandbox are Python-only, and
    `ast.parse` is the boring, zero-dependency, first-class way to do
    exactly this for one language."""
    started = time.monotonic()
    errors: dict[str, str] = {}
    parsed_count = 0

    for path, content in files_patched.items():
        if not path.endswith(_PYTHON_EXTENSIONS):
            continue
        parsed_count += 1
        try:
            ast.parse(content, filename=path)
        except SyntaxError as exc:
            errors[path] = str(exc)

    duration_ms = int((time.monotonic() - started) * 1000)
    return GateResult(
        gate="G1",
        passed=not errors,
        duration_ms=duration_ms,
        detail={"errors": errors} if errors else {"files_parsed": parsed_count},
    )
