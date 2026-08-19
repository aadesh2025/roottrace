"""S8 `validate` (`03` §S8, `07` §7) — the sandbox's own input/output
contract, from the orchestrator's side. `apps/sandbox-runner`'s
`io_contract.py` is the in-container mirror of this — the two packages
share no code (separate deployables, `settings.py`'s "duplicated, not
shared" precedent), only the JSON shape."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Language = Literal["python"]
ExpectedOutcome = Literal["fail", "pass"]
ValidationMode = Literal["full", "partial", "syntax_only"]
#: `03` §S8's nine gates. G0/G1 run host-side, before a container exists;
#: only G2-G8 are ever named in a `SandboxInput.gates` list.
GateName = Literal["G2", "G3", "G4", "G5", "G6", "G7", "G8"]


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RegressionTestRef(_Contract):
    path: str
    test_id: str
    expected_pre: ExpectedOutcome = "fail"
    expected_post: ExpectedOutcome = "pass"
    expected_error_family: str | None = None


class Manifest(_Contract):
    path: str
    content: str


class SandboxInput(_Contract):
    """`07` §7's literal input contract — what travels over the container's
    stdin after `start()` (§7's B10 note; corrected at T6.1 from the
    original `docker cp`-to-file design)."""

    validation_id: str
    language: Language
    language_version: str
    attempt: int = Field(ge=1)
    files_original: dict[str, str]
    files_patched: dict[str, str]
    new_files: dict[str, str] = Field(default_factory=dict)
    manifest: Manifest | None = None
    regression_test: RegressionTestRef | None = None
    existing_tests: tuple[str, ...] = ()
    gates: tuple[GateName, ...]
    budgets: dict[str, int]


class GateResult(_Contract):
    gate: str
    passed: bool
    duration_ms: int = Field(ge=0)
    detail: dict[str, object] = Field(default_factory=dict)


class ResourceUsage(_Contract):
    wall_ms: int = Field(ge=0)
    cpu_ms: int = Field(ge=0)
    peak_memory_mb: int = Field(ge=0)
    peak_pids: int = Field(ge=0)
    disk_written_mb: int = Field(ge=0)


class Transcript(_Contract):
    stdout_bytes: int = Field(ge=0)
    stderr_bytes: int = Field(ge=0)
    truncated: bool = False


class SignalsForScoring(_Contract):
    build_passed: bool
    regression_test_valid: bool
    test_pass_ratio: float | None = None
    new_static_findings_high: int = Field(ge=0, default=0)
    new_static_findings_medium: int = Field(ge=0, default=0)
    degraded_mode: bool = False


class ValidationResult(_Contract):
    """`07` §7's literal output contract — extracted from the container's
    captured stdout after `wait()` returns (corrected at T6.1 from the
    original `/work/_roottrace/result.json` `docker cp` design; `runner.py`
    still writes that file too, but nothing outside the container may
    depend on reading it back after exit)."""

    validation_id: str
    passed: bool
    mode: ValidationMode
    gates: tuple[GateResult, ...] = ()
    failed_gate: str | None = None
    resource_usage: ResourceUsage
    transcript: Transcript
    signals_for_scoring: SignalsForScoring
    error: str | None = None
