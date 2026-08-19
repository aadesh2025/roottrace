"""S8 `validate` (`03` §S8, `07` §7) — the sandbox's own input/output
contract, from the orchestrator's side. `apps/sandbox-runner`'s
`io_contract.py` is the in-container mirror of this — the two packages
share no code (separate deployables, `settings.py`'s "duplicated, not
shared" precedent), only the JSON shape."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from roottrace_worker.pipeline.understand.contracts import ExceptionFamily

Language = Literal["python"]
ExpectedOutcome = Literal["fail", "pass"]
ValidationMode = Literal["full", "partial", "syntax_only"]
#: `03` §S8's nine gates. G0/G1 run host-side, before a container exists;
#: only G2-G8 are ever named in a `SandboxInput.gates` list.
GateName = Literal["G2", "G3", "G4", "G5", "G6", "G7", "G8"]


class _Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RegressionTestRef(_Contract):
    """`expected_error_family` is typed against `03` §S4's real
    `ExceptionFamily` taxonomy, not a bare string — `07`'s own worked
    example originally used `"type_error"`, which was never one of the
    nine families (plus `unclassified`) that taxonomy actually defines;
    corrected at T6.4 alongside the JSON example in `07` §7."""

    path: str
    test_id: str
    expected_pre: ExpectedOutcome = "fail"
    expected_post: ExpectedOutcome = "pass"
    expected_error_family: ExceptionFamily | None = None


class Manifest(_Contract):
    path: str
    content: str


class SandboxInput(_Contract):
    """`07` §7's input contract — what travels over the container's stdin
    after `start()` (§7's B10 note; corrected at T6.1 from the original
    `docker cp`-to-file design).

    `existing_tests` corrected at T6.4 from `07`'s literal
    `["tests/test_checkout.py"]` (paths only) to `{path: content}`. G6
    ("tests discovered by S5 as covering the implicated symbols") runs
    tests that were never touched by the diff at all — that's the entire
    point, catching a regression in code the patch didn't mean to affect —
    so their content cannot be assumed to already be sitting in
    `files_original`/`files_patched`, which are scoped to only the files
    the diff *does* touch. The container has no filesystem access to fetch
    it separately (no network, no host mounts), so it has to travel in
    this same stdin payload or G6 cannot run at all."""

    validation_id: str
    language: Language
    language_version: str
    attempt: int = Field(ge=1)
    files_original: dict[str, str]
    files_patched: dict[str, str]
    new_files: dict[str, str] = Field(default_factory=dict)
    manifest: Manifest | None = None
    regression_test: RegressionTestRef | None = None
    existing_tests: dict[str, str] = Field(default_factory=dict)
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
    """`regression_test_valid` is a tri-state, not a plain bool: `None`
    means G4 did not run at all (degraded `mode`, T6.5), which is a
    different claim from G4 running and reporting the test invalid. S11
    (Phase 13, not yet built) is the actual consumer of
    `validation_component_cap`/`band_cap` — `07` §5's degraded-mode table
    names the cap values (0.55 / low-band 0.35); this stage only proves the
    cap was genuinely triggered by a real cache-coverage gap, not compute
    the capped score itself, which needs the rest of S11's formula."""

    build_passed: bool
    regression_test_valid: bool | None = None
    test_pass_ratio: float | None = None
    new_static_findings_high: int = Field(ge=0, default=0)
    new_static_findings_medium: int = Field(ge=0, default=0)
    degraded_mode: bool = False
    validation_component_cap: float | None = None
    band_cap: Literal["low"] | None = None


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
