"""`io_contract.py` (T6.1) — the input/output contract `docs/07` §7 defines,
read and written with the standard library only. No container needed."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from roottrace_sandbox_runner.io_contract import (
    RESULT_STDOUT_END,
    RESULT_STDOUT_START,
    InvalidInputError,
    emit_result_to_stdout,
    read_input,
    read_input_from_stdin,
    write_result,
)

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


def test_reads_a_well_formed_input(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_text(json.dumps(_valid_input()), encoding="utf-8")

    data = read_input(path)

    assert data["validation_id"] == "val_1"
    assert data["gates"] == []


def test_missing_input_file_raises_invalid_input_error(tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError, match="no input staged"):
        read_input(tmp_path / "missing.json")


def test_non_object_input_raises_invalid_input_error(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(InvalidInputError, match="JSON object"):
        read_input(path)


def test_missing_required_key_raises_invalid_input_error(tmp_path: Path) -> None:
    payload = _valid_input()
    del payload["gates"]
    path = tmp_path / "input.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(InvalidInputError, match="gates"):
        read_input(path)


def test_malformed_json_raises_invalid_input_error(tmp_path: Path) -> None:
    path = tmp_path / "input.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(InvalidInputError, match="not valid JSON"):
        read_input(path)


def test_reads_a_well_formed_input_from_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_valid_input())))

    data = read_input_from_stdin()

    assert data["validation_id"] == "val_1"


def test_write_result_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "_roottrace" / "result.json"
    write_result({"passed": True}, path)

    assert json.loads(path.read_text(encoding="utf-8")) == {"passed": True}


def test_emit_result_to_stdout_is_delimited_and_round_trips(
    capsys: pytest.CaptureFixture[str],
) -> None:
    emit_result_to_stdout({"passed": True, "validation_id": "val_1"})

    out = capsys.readouterr().out
    assert RESULT_STDOUT_START in out
    assert RESULT_STDOUT_END in out
    start = out.index(RESULT_STDOUT_START) + len(RESULT_STDOUT_START)
    end = out.index(RESULT_STDOUT_END)
    assert json.loads(out[start:end]) == {"passed": True, "validation_id": "val_1"}
