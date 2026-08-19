"""`runner.py` (T6.1) — the process the sandbox image's `ENTRYPOINT` invokes,
exercised directly against explicit paths under `tmp_path`. No container
needed; the container-level round-trip (does the image actually run this
and produce a readable result) is covered by the integration tests in
`apps/worker/tests/test_validate_orchestrator.py`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roottrace_sandbox_runner import runner

pytestmark = pytest.mark.unit


def _valid_input(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "validation_id": "val_1",
        "language": "python",
        "files_original": {"a.py": "x = 1\n"},
        "files_patched": {"a.py": "x = 2\n"},
        "gates": [],
        "budgets": {"total_s": 45},
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def sandbox_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    input_path = tmp_path / "opt" / "roottrace" / "input.json"
    input_path.parent.mkdir(parents=True)
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    result_path = work_dir / "_roottrace" / "result.json"
    return input_path, work_dir, result_path


def test_a_well_formed_input_with_no_requested_gates_round_trips(
    sandbox_paths: tuple[Path, Path, Path],
) -> None:
    input_path, work_dir, result_path = sandbox_paths
    input_path.write_text(json.dumps(_valid_input()), encoding="utf-8")

    exit_code = runner.main(input_path=input_path, work_dir=work_dir, result_path=result_path)

    assert exit_code == 0
    assert (work_dir / "a.py").read_text(encoding="utf-8") == "x = 2\n"  # files_patched
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["passed"] is True
    assert result["validation_id"] == "val_1"
    assert result["gates"] == []
    assert result["resource_usage"]["wall_ms"] >= 0


def test_a_requested_but_unimplemented_gate_fails_honestly(
    sandbox_paths: tuple[Path, Path, Path],
) -> None:
    """`_GATE_DISPATCH` is empty until T6.4 — requesting a real gate name
    must fail loudly, never silently report `passed: true` with zero gates
    actually run. This is the exact "green without gating" failure mode
    `CLAUDE.md`'s testing standard exists to prevent."""
    input_path, work_dir, result_path = sandbox_paths
    input_path.write_text(json.dumps(_valid_input(gates=["G2"])), encoding="utf-8")

    exit_code = runner.main(input_path=input_path, work_dir=work_dir, result_path=result_path)

    assert exit_code == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["passed"] is False
    assert "not yet implemented" in result["error"]


def test_missing_input_writes_an_honest_failure_result(
    sandbox_paths: tuple[Path, Path, Path],
) -> None:
    input_path, work_dir, result_path = sandbox_paths
    # Deliberately not writing input.json.

    exit_code = runner.main(input_path=input_path, work_dir=work_dir, result_path=result_path)

    assert exit_code == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["passed"] is False
    assert result["validation_id"] == "unknown"


def test_a_path_escaping_work_dir_in_files_patched_fails_honestly(
    sandbox_paths: tuple[Path, Path, Path],
) -> None:
    input_path, work_dir, result_path = sandbox_paths
    input_path.write_text(
        json.dumps(_valid_input(files_patched={"../escape.py": "evil = True\n"})), encoding="utf-8"
    )

    exit_code = runner.main(input_path=input_path, work_dir=work_dir, result_path=result_path)

    assert exit_code == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["passed"] is False
    assert "resolves outside" in result["error"]


def test_main_from_stdin_round_trips(
    sandbox_paths: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real `ENTRYPOINT` path (`io_contract`'s module docstring: stdin,
    not `docker cp`, for the empirically-verified reason)."""
    import io

    _input_path, work_dir, result_path = sandbox_paths
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_valid_input())))

    exit_code = runner.main_from_stdin(work_dir=work_dir, result_path=result_path)

    assert exit_code == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["passed"] is True
    assert result["validation_id"] == "val_1"


def test_main_from_stdin_with_invalid_input_fails_honestly(
    sandbox_paths: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    _input_path, work_dir, result_path = sandbox_paths
    monkeypatch.setattr("sys.stdin", io.StringIO("not valid json"))

    exit_code = runner.main_from_stdin(work_dir=work_dir, result_path=result_path)

    assert exit_code == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["passed"] is False
    assert result["validation_id"] == "unknown"
