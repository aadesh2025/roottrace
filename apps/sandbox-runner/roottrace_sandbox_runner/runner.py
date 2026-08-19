"""The process `apps/sandbox-runner/python/Dockerfile`'s `ENTRYPOINT` runs
(`docs/07` §4, §7, §8). Reads the staged input, materialises the working
tree, executes whichever gates the caller requested, and writes the result.

**No gate is implemented yet — deliberately, this ticket.** T6.1-T6.3 build
the image, the orchestration that calls it, and the isolation the call
happens under; `_GATE_DISPATCH` below is the seam T6.4 (`07` §6, the nine
gates) fills in. Requesting a gate this dispatch table does not know about
is a hard failure, not a silent skip — a validation that silently ran zero
gates and reported `passed: true` would be exactly the "green without
actually gating" failure mode `CLAUDE.md`'s testing standard exists to
prevent, so an empty/unimplemented gate list is the only value this
process will accept until T6.4 fills the table in.

**Real container use is `main_from_stdin`, not `main`.** `main` stays
file-based — simple to unit-test, and a reasonable primitive for any future
caller with a reason to stage input on disk — but the actual `ENTRYPOINT`
(see the Dockerfile) reads its input over stdin; `io_contract`'s module
docstring has the empirically-verified reason `docker cp` to a file does
not work against a read-only container."""

from __future__ import annotations

import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from roottrace_sandbox_runner.io_contract import (
    RESULT_PATH,
    WORK_DIR,
    InvalidInputError,
    emit_result_to_stdout,
    read_input,
    read_input_from_stdin,
    write_result,
)
from roottrace_sandbox_runner.materialize import materialize_tree

GateFn = Callable[[dict[str, Any], Path], dict[str, Any]]

#: Filled in at T6.4. Every name `07` §6 defines (G2-G8; G0/G1 run host-side,
#: before a container ever starts) is a future key here.
_GATE_DISPATCH: dict[str, GateFn] = {}


def _run_gates(
    gate_names: list[str], bundle: dict[str, Any], work_dir: Path
) -> list[dict[str, Any]]:
    unknown = [name for name in gate_names if name not in _GATE_DISPATCH]
    if unknown:
        raise NotImplementedError(
            f"gate(s) not yet implemented: {unknown} — the nine gates are T6.4, "
            "not built by this image yet"
        )
    return [_GATE_DISPATCH[name](bundle, work_dir) for name in gate_names]


def _error_result(validation_id: str, wall_ms: int, message: str) -> dict[str, Any]:
    return {
        "validation_id": validation_id,
        "passed": False,
        "mode": "full",
        "gates": [],
        "failed_gate": "runner_error",
        "resource_usage": {
            "wall_ms": wall_ms,
            "cpu_ms": 0,
            "peak_memory_mb": 0,
            "peak_pids": 0,
            "disk_written_mb": 0,
        },
        "transcript": {"stdout_bytes": 0, "stderr_bytes": len(message), "truncated": False},
        "signals_for_scoring": {
            "build_passed": False,
            "regression_test_valid": False,
            "test_pass_ratio": None,
            "new_static_findings_high": 0,
            "new_static_findings_medium": 0,
            "degraded_mode": False,
        },
        "error": message,
    }


def _finish(result: dict[str, Any], result_path: Path) -> None:
    write_result(result, result_path)
    emit_result_to_stdout(result)


def _process(bundle: dict[str, Any], *, work_dir: Path, result_path: Path) -> int:
    started = time.monotonic()

    def wall_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        materialize_tree(work_dir, bundle["files_patched"])
        gates = _run_gates(list(bundle["gates"]), bundle, work_dir)
    except Exception as exc:  # this process must never crash silently
        _finish(
            _error_result(bundle["validation_id"], wall_ms(), f"{exc}\n{traceback.format_exc()}"),
            result_path,
        )
        return 1

    result = {
        "validation_id": bundle["validation_id"],
        "passed": True,
        "mode": "full",
        "gates": gates,
        "failed_gate": None,
        "resource_usage": {
            # Real cpu_ms/peak_memory_mb/peak_pids/disk_written_mb tracking
            # is added alongside the gates that actually spawn subprocesses
            # to measure (T6.4) — reporting fabricated numbers here would be
            # exactly the kind of check that only looks real.
            "wall_ms": wall_ms(),
            "cpu_ms": 0,
            "peak_memory_mb": 0,
            "peak_pids": 0,
            "disk_written_mb": 0,
        },
        "transcript": {"stdout_bytes": 0, "stderr_bytes": 0, "truncated": False},
        "signals_for_scoring": {
            "build_passed": True,
            "regression_test_valid": False,
            "test_pass_ratio": None,
            "new_static_findings_high": 0,
            "new_static_findings_medium": 0,
            "degraded_mode": False,
        },
    }
    _finish(result, result_path)
    return 0


def main(*, input_path: Path, work_dir: Path = WORK_DIR, result_path: Path = RESULT_PATH) -> int:
    try:
        bundle = read_input(input_path)
    except InvalidInputError as exc:
        # No `validation_id` to report if the input itself couldn't be read.
        _finish(_error_result("unknown", 0, str(exc)), result_path)
        return 1
    return _process(bundle, work_dir=work_dir, result_path=result_path)


def main_from_stdin(*, work_dir: Path = WORK_DIR, result_path: Path = RESULT_PATH) -> int:
    try:
        bundle = read_input_from_stdin()
    except InvalidInputError as exc:
        _finish(_error_result("unknown", 0, str(exc)), result_path)
        return 1
    return _process(bundle, work_dir=work_dir, result_path=result_path)


if __name__ == "__main__":
    sys.exit(main_from_stdin())
