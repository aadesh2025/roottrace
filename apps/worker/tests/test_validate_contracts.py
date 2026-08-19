"""`contracts.py` (T6.2, `07` §7) — the sandbox input/output contract,
validated as a pure Pydantic model. No container needed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from roottrace_worker.pipeline.validate.contracts import (
    ResourceUsage,
    SandboxInput,
    SignalsForScoring,
    Transcript,
    ValidationResult,
)

pytestmark = pytest.mark.unit


def test_a_well_formed_sandbox_input_validates() -> None:
    bundle = SandboxInput(
        validation_id="val_1",
        language="python",
        language_version="3.12",
        attempt=1,
        files_original={"a.py": "x = 1\n"},
        files_patched={"a.py": "x = 2\n"},
        gates=("G2", "G3"),
        budgets={"total_s": 45},
    )
    assert bundle.gates == ("G2", "G3")
    assert bundle.new_files == {}


def test_an_unregistered_gate_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SandboxInput(
            validation_id="val_1",
            language="python",
            language_version="3.12",
            attempt=1,
            files_original={},
            files_patched={},
            gates=("G99",),  # type: ignore[arg-type]
            budgets={},
        )


def test_attempt_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        SandboxInput(
            validation_id="val_1",
            language="python",
            language_version="3.12",
            attempt=0,
            files_original={},
            files_patched={},
            gates=(),
            budgets={},
        )


def test_a_well_formed_validation_result_round_trips_through_json() -> None:
    result = ValidationResult(
        validation_id="val_1",
        passed=True,
        mode="full",
        resource_usage=ResourceUsage(
            wall_ms=100, cpu_ms=50, peak_memory_mb=10, peak_pids=2, disk_written_mb=1
        ),
        transcript=Transcript(stdout_bytes=10, stderr_bytes=0, truncated=False),
        signals_for_scoring=SignalsForScoring(build_passed=True, regression_test_valid=True),
    )
    reparsed = ValidationResult.model_validate_json(result.model_dump_json())
    assert reparsed == result
