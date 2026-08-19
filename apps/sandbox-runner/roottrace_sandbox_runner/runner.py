"""The process `apps/sandbox-runner/python/Dockerfile`'s `ENTRYPOINT` runs
(`docs/07` §4, §7, §8). Reads the staged input, executes whichever gates
the caller requested, and writes the result.

**T6.4: `_GATE_DISPATCH` is now filled in — G2 through G8, `gates.py`.**
Requesting a gate this dispatch table does not know about is still a hard
failure, not a silent skip, for the same reason it always was: a
validation that silently ran zero gates and reported `passed: true` would
be exactly the "green without actually gating" failure mode `CLAUDE.md`'s
testing standard exists to prevent.

**Fail-fast, `03` §S8's own ordering.** The first gate that fails stops
the sequence — `07` §6 orders G2-G8 cheapest/most-informative first for
exactly this reason, and there is no reason to spend a container's budget
running G7 static analysis against code that failed G3's import check.
Each gate materialises its own required tree (original vs patched, see
`gates.py`'s module docstring) rather than this function doing one
upfront materialisation that would be wrong for half of them.

**Real container use is `main_from_stdin`, not `main`.** `main` stays
file-based — simple to unit-test, and a reasonable primitive for any future
caller with a reason to stage input on disk — but the actual `ENTRYPOINT`
(see the Dockerfile) reads its input over stdin; `io_contract`'s module
docstring has the empirically-verified reason `docker cp` to a file does
not work against a read-only container.

**T6.5: `_run_gates` now tracks `mode`, not just `passed`/`failed_gate`.**
G2 (`gates.py`'s `gate_dependencies`) is the one gate that can report
`full`/`partial`/`syntax_only` in its own detail — a real cache-coverage
gap is never treated as the kind of failure that stops the sequence
(`07` §5: "we do not fail the whole validation"). Once G2 has reported a
degraded mode, `_MODE_ALLOWED_EXTRA` decides which of the remaining
requested gates are still worth attempting; everything else is marked
`degraded_skip` and reports `passed: True` — a skip, not a fabricated
pass — so `_signals_for_scoring` can tell the difference between "this
gate ran and passed" and "this gate never ran," which is the whole point
of a mode field that downstream code can actually trust."""

from __future__ import annotations

import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from roottrace_sandbox_runner.gates import (
    gate_compile,
    gate_dependencies,
    gate_existing_tests,
    gate_regression_post,
    gate_regression_pre,
    gate_security_scan,
    gate_static_analysis,
)
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

#: `07` §6's own order — cheapest/most-informative first, fail-fast.
_GATE_DISPATCH: dict[str, GateFn] = {
    "G2": gate_dependencies,
    "G3": gate_compile,
    "G4": gate_regression_pre,
    "G5": gate_regression_post,
    "G6": gate_existing_tests,
    "G7": gate_static_analysis,
    "G8": gate_security_scan,
}

#: `07` §5's degraded-mode table — which requested gates (other than G2
#: itself, which always runs for real and is what *reports* the mode) are
#: still worth attempting once G2 has found a cache-coverage gap. Every
#: other requested gate is skipped honestly (`degraded_skip: True` in its
#: detail, `passed: True` since a skip is not a failure — same "nothing to
#: check" convention `gate_existing_tests`/`gate_dependencies` already use
#: for "no existing tests"/"no manifest") rather than attempted against
#: dependencies we already know are not there.
_MODE_ALLOWED_EXTRA: dict[str, frozenset[str]] = {
    "partial": frozenset({"G3", "G7", "G8"}),
    "syntax_only": frozenset({"G7"}),
}

#: `07` §5's cap values, carried on `signals_for_scoring` for S11 (Phase
#: 13, not yet built) to apply — this stage proves the cap was genuinely
#: triggered by a real cache-coverage gap; it does not compute the capped
#: score itself, which needs the rest of S11's formula.
_VALIDATION_COMPONENT_CAPS: dict[str, tuple[float | None, str | None]] = {
    "full": (None, None),
    "partial": (0.55, None),
    "syntax_only": (0.35, "low"),
}


def _degraded_skip_result(gate: str, mode: str) -> dict[str, Any]:
    return {
        "gate": gate,
        "passed": True,
        "duration_ms": 0,
        "detail": {
            "degraded_skip": True,
            "reason": f"sandbox mode is {mode!r} (07 S5) — this gate does not run in degraded mode",
        },
    }


def _run_gates(
    gate_names: list[str], bundle: dict[str, Any], work_dir: Path
) -> tuple[list[dict[str, Any]], str | None, str]:
    unknown = [name for name in gate_names if name not in _GATE_DISPATCH]
    if unknown:
        raise NotImplementedError(f"gate(s) not recognised: {unknown}")

    results: list[dict[str, Any]] = []
    mode = "full"
    for name in gate_names:
        if (
            name != "G2"
            and mode != "full"
            and name not in _MODE_ALLOWED_EXTRA.get(mode, frozenset())
        ):
            results.append(_degraded_skip_result(name, mode))
            continue
        result = _GATE_DISPATCH[name](bundle, work_dir)
        results.append(result)
        if name == "G2":
            mode = result["detail"].get("mode", "full")
        if not result["passed"]:
            return results, name, mode
    return results, None, mode


def _signals_for_scoring(gates: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    by_gate = {g["gate"]: g for g in gates}

    def ran(name: str) -> dict[str, Any] | None:
        """A gate that ran for real — `None` both when the gate wasn't
        requested at all and when it was skipped for being out of scope in
        degraded mode (`_degraded_skip_result`); either way, nothing to
        read a real signal from."""
        g = by_gate.get(name)
        if g is None or g["detail"].get("degraded_skip"):
            return None
        return g

    g2, g3 = ran("G2"), ran("G3")
    g4 = ran("G4")
    g6 = ran("G6")
    g7 = ran("G7")

    # T6.5: `syntax_only` is not just "G3 didn't run" — it is G2's own
    # `_core_imports_ok` check having already found the application source
    # cannot be imported (that is *why* the mode is `syntax_only`). Letting
    # `build_passed` fall back to "vacuously true, nothing said otherwise"
    # here would assert the opposite of a fact this stage already knows.
    build_passed = (
        False if mode == "syntax_only" else all(g["passed"] for g in (g2, g3) if g is not None)
    )
    # `03` §S8/`06` §7.3: "regression_test_valid" means G4 proved the test
    # is real (fails on unpatched code) — it says nothing about whether G5
    # then passed, which is a separate signal this dict doesn't carry a
    # field for (`failed_gate` already tells the caller if G5 was the one
    # that stopped the sequence). `None`, not `False`, when G4 never ran —
    # T6.5: a degraded-mode skip is not the same claim as G4 running and
    # finding the test invalid.
    regression_test_valid = bool(g4["passed"]) if g4 is not None else None

    test_pass_ratio = None
    if g6 is not None:
        total = g6["detail"].get("total", 0)
        passed_count = g6["detail"].get("passed", 0)
        test_pass_ratio = (passed_count / total) if total else None

    new_high = g7["detail"].get("new_high", 0) if g7 is not None else 0
    new_medium = g7["detail"].get("new_medium", 0) if g7 is not None else 0

    cap, band_cap = _VALIDATION_COMPONENT_CAPS.get(mode, (None, None))

    return {
        "build_passed": build_passed,
        "regression_test_valid": regression_test_valid,
        "test_pass_ratio": test_pass_ratio,
        "new_static_findings_high": new_high,
        "new_static_findings_medium": new_medium,
        "degraded_mode": mode != "full",
        "validation_component_cap": cap,
        "band_cap": band_cap,
    }


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
            "regression_test_valid": None,
            "test_pass_ratio": None,
            "new_static_findings_high": 0,
            "new_static_findings_medium": 0,
            "degraded_mode": False,
            "validation_component_cap": None,
            "band_cap": None,
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
        gates, failed_gate, mode = _run_gates(list(bundle["gates"]), bundle, work_dir)
        # Gates that need the original tree (G4, G6/G7's "pre" runs) leave
        # /work in whatever state they last materialised — reset to the
        # patched tree as the final, predictable state regardless of which
        # gate ran last or whether any gate ran at all.
        materialize_tree(work_dir, bundle["files_patched"])
    except Exception as exc:  # this process must never crash silently
        _finish(
            _error_result(bundle["validation_id"], wall_ms(), f"{exc}\n{traceback.format_exc()}"),
            result_path,
        )
        return 1

    result = {
        "validation_id": bundle["validation_id"],
        "passed": failed_gate is None,
        "mode": mode,
        "gates": gates,
        "failed_gate": failed_gate,
        "resource_usage": {
            # Real cpu_ms/peak_memory_mb/peak_pids/disk_written_mb tracking
            # would need cgroup introspection this process does not have
            # reason to add yet — reporting fabricated numbers here would
            # be exactly the kind of check that only looks real.
            "wall_ms": wall_ms(),
            "cpu_ms": 0,
            "peak_memory_mb": 0,
            "peak_pids": 0,
            "disk_written_mb": 0,
        },
        "transcript": {"stdout_bytes": 0, "stderr_bytes": 0, "truncated": False},
        "signals_for_scoring": _signals_for_scoring(gates, mode),
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
