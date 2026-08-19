"""Pure-function pieces of `orchestrator.py` (T6.2) that don't need a real
container to exercise — the isolation config builder, result-marker
parsing, and transcript sanitisation. The container-level behaviour these
functions feed into is covered by `test_validate_orchestrator.py`
(real Docker, marked `integration`)."""

from __future__ import annotations

from typing import Any

import pytest

from roottrace_worker.pipeline.validate.contracts import ValidationResult
from roottrace_worker.pipeline.validate.orchestrator import (
    RESULT_STDOUT_END,
    RESULT_STDOUT_START,
    ResultExtractionError,
    SandboxOrchestrator,
    _build_create_config,
    _extract_result,
)

pytestmark = pytest.mark.unit


def _build(**overrides: object) -> dict[str, Any]:
    kwargs: dict[str, object] = {
        "image": "roottrace/sandbox-python:3.12",
        "memory_limit_mb": 512,
        "cpu_limit": 1.0,
        "pids_limit": 128,
        "disk_limit_mb": 256,
        "runtime": "runsc",
        "apparmor_profile": None,
        "validation_id": "val_1",
    }
    kwargs.update(overrides)
    return _build_create_config(**kwargs)  # type: ignore[arg-type]


def test_every_07_s3_isolation_flag_is_present() -> None:
    config = _build()
    host = config["HostConfig"]
    assert host["NetworkMode"] == "none"
    assert host["ReadonlyRootfs"] is True
    assert host["CapDrop"] == ["ALL"]
    assert host["CapAdd"] == []
    assert host["Privileged"] is False
    assert config["User"] == "65534:65534"
    assert "no-new-privileges:true" in host["SecurityOpt"]


def test_memory_swap_equals_memory_so_swap_is_disabled() -> None:
    config = _build(memory_limit_mb=512)
    host = config["HostConfig"]
    assert host["Memory"] == host["MemorySwap"] == 512 * 1024 * 1024


def test_runsc_runtime_is_set_when_requested() -> None:
    config = _build(runtime="runsc")
    assert config["HostConfig"]["Runtime"] == "runsc"


def test_runc_runtime_omits_the_runtime_key_entirely() -> None:
    """`Runtime` unset lets the daemon use its own default (`runc`) —
    passing `"runc"` explicitly is not a recognised override value on
    every Docker installation, so omitting the key is the honest way to
    say "no gVisor.\""""
    config = _build(runtime="runc")
    assert "Runtime" not in config["HostConfig"]


def test_an_apparmor_profile_is_appended_to_security_opt_when_configured() -> None:
    config = _build(apparmor_profile="roottrace-sandbox")
    assert "apparmor=roottrace-sandbox" in config["HostConfig"]["SecurityOpt"]


def test_no_apparmor_override_when_unconfigured() -> None:
    """Docker's own `docker-default` AppArmor confinement still applies
    automatically — see `settings.py`'s `sandbox_apparmor_profile`."""
    config = _build(apparmor_profile=None)
    assert not any(opt.startswith("apparmor=") for opt in config["HostConfig"]["SecurityOpt"])


def test_stdin_is_open_for_input_delivery() -> None:
    config = _build()
    assert config["OpenStdin"] is True
    assert config["StdinOnce"] is True


def _delimited(payload: str) -> str:
    return f"some gate chatter\n{RESULT_STDOUT_START}\n{payload}\n{RESULT_STDOUT_END}\ntrailing\n"


def test_a_well_formed_result_is_extracted_from_surrounding_log_noise() -> None:
    log = _delimited(
        '{"validation_id": "val_1", "passed": true, "mode": "full", '
        '"resource_usage": {"wall_ms": 1, "cpu_ms": 0, "peak_memory_mb": 0, '
        '"peak_pids": 0, "disk_written_mb": 0}, '
        '"transcript": {"stdout_bytes": 0, "stderr_bytes": 0, "truncated": false}, '
        '"signals_for_scoring": {"build_passed": true, "regression_test_valid": false}}'
    )
    result = _extract_result(log, validation_id="val_1")
    assert isinstance(result, ValidationResult)
    assert result.passed is True


def test_missing_markers_raise_result_extraction_error() -> None:
    with pytest.raises(ResultExtractionError, match="no result markers"):
        _extract_result("no markers here at all", validation_id="val_1")


def test_malformed_json_between_markers_raises_result_extraction_error() -> None:
    with pytest.raises(ResultExtractionError, match="not valid JSON"):
        _extract_result(_delimited("{not valid json"), validation_id="val_1")


def test_sanitize_transcript_strips_and_reports_byte_count() -> None:
    orch = SandboxOrchestrator(docker=object(), image="x", max_stdout_bytes=1000)  # type: ignore[arg-type]
    transcript = orch.sanitize_transcript("\x1b[31mhello\x1b[0m\x00world")
    assert transcript.stdout_bytes == len(b"helloworld")
    assert not transcript.truncated
