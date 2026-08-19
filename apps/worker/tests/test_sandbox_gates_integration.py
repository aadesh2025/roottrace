"""G0-G8 (`03` §S8, `07` §6, T6.4), exercised end to end: host-side G0/G1
(`pipeline/validate/gates.py`) feeding a real container running G2-G8
(`apps/sandbox-runner/roottrace_sandbox_runner/gates.py`), via the real
`SandboxOrchestrator` and the real `roottrace/sandbox-python:3.12` image —
never mocked, for the same reason every other T6.x integration suite gives.

**G4 gets the most scrutiny here, deliberately.** `CLAUDE.md`'s testing
standard — "a test that passes both before and after proves nothing" — is
exactly what G4 exists to enforce on every patch this pipeline will ever
produce, so this suite proves G4 actually enforces it: a genuinely
reproducing test is accepted, a test that passes on unpatched code is
rejected, and a test that fails for an unrelated reason (broken import,
not the bug) is rejected too — not just the single "happy path" case.

Skipped when Docker is unreachable or the image has not been built — see
`test_validate_orchestrator.py`'s module docstring for the build command."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from typing import Any

import aiodocker
import pytest

from roottrace_worker.pipeline.validate import SandboxInput, SandboxOrchestrator
from roottrace_worker.pipeline.validate.gates import check_diff_applies, check_syntax

IMAGE = "roottrace/sandbox-python:3.12"


def _detail(gate_detail: Mapping[str, object], key: str) -> Any:
    """`GateResult.detail` is deliberately typed `dict[str, object]` (`03`
    §S8's per-gate detail shape is free-form) — this narrows one key back
    to something usable without an `isinstance` check at every call site."""
    return gate_detail[key]


async def _docker_and_image_available() -> bool:
    try:
        docker = aiodocker.Docker()
        try:
            await docker.images.inspect(IMAGE)
            return True
        finally:
            await docker.close()
    except Exception:
        return False


_AVAILABLE = asyncio.run(_docker_and_image_available())

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _AVAILABLE,
        reason=f"Docker unreachable or {IMAGE} not built — see test_validate_orchestrator.py",
    ),
]


@pytest.fixture
async def docker() -> AsyncIterator[aiodocker.Docker]:
    client = aiodocker.Docker()
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture
def orchestrator(docker: aiodocker.Docker) -> SandboxOrchestrator:
    return SandboxOrchestrator(docker=docker, image=IMAGE, runtime="runc", timeout_seconds=45)


ORIGINAL_CHECKOUT = (
    "def calculate_total(base_price, tax_amount):\n"
    "    subtotal = base_price + tax_amount\n"
    "    return subtotal\n"
)

REAL_FIX_DIFF = (
    "--- a/checkout.py\n+++ b/checkout.py\n@@ -1,3 +1,3 @@\n"
    " def calculate_total(base_price, tax_amount):\n"
    "-    subtotal = base_price + tax_amount\n"
    "+    subtotal = base_price + (tax_amount or 0)\n"
    "     return subtotal\n"
)

GENUINE_REGRESSION_TEST = (
    "from checkout import calculate_total\n\n"
    "def test_calculate_total_handles_none_tax():\n"
    "    assert calculate_total(100, None) == 100\n"
)


def _bundle(**overrides: object) -> SandboxInput:
    payload: dict[str, object] = {
        "validation_id": "val_integration",
        "language": "python",
        "language_version": "3.12",
        "attempt": 1,
        "files_original": {"checkout.py": ORIGINAL_CHECKOUT},
        "files_patched": {},
        "gates": (),
        "budgets": {"total_s": 45, "deps_s": 10, "tests_s": 20, "static_s": 10},
    }
    payload.update(overrides)
    return SandboxInput.model_validate(payload)


async def test_a_genuine_fix_passes_every_gate(orchestrator: SandboxOrchestrator) -> None:
    g0, files_patched = check_diff_applies(REAL_FIX_DIFF, {"checkout.py": ORIGINAL_CHECKOUT})
    assert g0.passed
    assert files_patched is not None
    assert check_syntax(files_patched).passed

    existing_test = (
        "from checkout import calculate_total\n\n"
        "def test_calculate_total_basic():\n"
        "    assert calculate_total(100, 10) == 110\n"
    )
    bundle = _bundle(
        files_patched=files_patched,
        new_files={"tests/test_regression.py": GENUINE_REGRESSION_TEST},
        regression_test={
            "path": "tests/test_regression.py",
            "test_id": "tests/test_regression.py::test_calculate_total_handles_none_tax",
            "expected_pre": "fail",
            "expected_post": "pass",
            "expected_error_family": "type_mismatch",
        },
        existing_tests={"tests/test_existing.py": existing_test},
        gates=("G2", "G3", "G4", "G5", "G6", "G7", "G8"),
    )
    result = await orchestrator.run(bundle)

    assert result.passed, result.gates
    assert result.failed_gate is None
    gate_names = [g.gate for g in result.gates]
    assert gate_names == ["G2", "G3", "G4", "G5", "G6", "G7", "G8"]
    assert result.signals_for_scoring.regression_test_valid is True
    assert result.signals_for_scoring.test_pass_ratio == 1.0


async def test_g4_rejects_a_test_that_passes_on_unpatched_code(
    orchestrator: SandboxOrchestrator,
) -> None:
    """The single most important property this whole gate sequence
    exists to enforce (`CLAUDE.md`): a test that does not fail on the
    original, buggy code proves nothing and must not be accepted."""
    already_fixed = "def calculate_total(base_price, tax_amount):\n    subtotal = base_price + (tax_amount or 0)\n    return subtotal\n"
    noop_diff = (
        "--- a/checkout.py\n+++ b/checkout.py\n@@ -1,3 +1,3 @@\n"
        " def calculate_total(base_price, tax_amount):\n"
        "-    subtotal = base_price + (tax_amount or 0)\n"
        "+    subtotal = base_price + (tax_amount or 0)  # comment only\n"
        "     return subtotal\n"
    )
    g0, files_patched = check_diff_applies(noop_diff, {"checkout.py": already_fixed})
    assert g0.passed
    assert files_patched is not None

    theatrical_test = (
        "from checkout import calculate_total\n\n"
        "def test_theatrical():\n"
        "    assert calculate_total(100, None) == 100\n"
    )
    bundle = _bundle(
        files_original={"checkout.py": already_fixed},
        files_patched=files_patched,
        new_files={"tests/test_regression.py": theatrical_test},
        regression_test={
            "path": "tests/test_regression.py",
            "test_id": "tests/test_regression.py::test_theatrical",
            "expected_pre": "fail",
            "expected_post": "pass",
        },
        gates=("G4", "G5"),
    )
    result = await orchestrator.run(bundle)

    assert not result.passed
    assert result.failed_gate == "G4"
    assert "PASSED on unpatched code" in _detail(result.gates[0].detail, "reason")
    assert result.signals_for_scoring.regression_test_valid is False


async def test_g4_rejects_a_test_that_fails_for_an_unrelated_reason(
    orchestrator: SandboxOrchestrator,
) -> None:
    """`07` §6: "If it fails with an *unrelated* error ... that is also a
    failure: the test is broken, not demonstrative." A `ModuleNotFoundError`
    from a typo'd import must not be mistaken for the bug reproducing."""
    g0, files_patched = check_diff_applies(REAL_FIX_DIFF, {"checkout.py": ORIGINAL_CHECKOUT})
    assert g0.passed
    assert files_patched is not None

    broken_test = "from this_module_does_not_exist import thing\n\ndef test_x():\n    pass\n"
    bundle = _bundle(
        files_patched=files_patched,
        new_files={"tests/test_regression.py": broken_test},
        regression_test={
            "path": "tests/test_regression.py",
            "test_id": "tests/test_regression.py::test_x",
            "expected_pre": "fail",
            "expected_post": "pass",
        },
        gates=("G4",),
    )
    result = await orchestrator.run(bundle)

    assert not result.passed
    assert result.failed_gate == "G4"
    assert result.gates[0].detail["exception_type"] == "ModuleNotFoundError"
    assert "broken, not demonstrative" in _detail(result.gates[0].detail, "reason")


async def test_g5_rejects_a_patch_that_does_not_actually_fix_the_bug(
    orchestrator: SandboxOrchestrator,
) -> None:
    """G4 passing only proves the test reproduces the bug; G5 is what
    proves the *fix* resolves it. `07` §6: failure here routes to S6, not
    S7 — "the diagnosis was wrong," not just the code (S9's routing
    itself is T7.1, out of this ticket's scope)."""
    non_fix_diff = (
        "--- a/checkout.py\n+++ b/checkout.py\n@@ -1,3 +1,3 @@\n"
        " def calculate_total(base_price, tax_amount):\n"
        "-    subtotal = base_price + tax_amount\n"
        "+    subtotal = base_price + tax_amount  # does not fix anything\n"
        "     return subtotal\n"
    )
    g0, files_patched = check_diff_applies(non_fix_diff, {"checkout.py": ORIGINAL_CHECKOUT})
    assert g0.passed
    assert files_patched is not None

    bundle = _bundle(
        files_patched=files_patched,
        new_files={"tests/test_regression.py": GENUINE_REGRESSION_TEST},
        regression_test={
            "path": "tests/test_regression.py",
            "test_id": "tests/test_regression.py::test_calculate_total_handles_none_tax",
            "expected_pre": "fail",
            "expected_post": "pass",
        },
        gates=("G4", "G5"),
    )
    result = await orchestrator.run(bundle)

    assert not result.passed
    assert result.failed_gate == "G5"
    assert result.gates[0].passed  # G4 correctly confirmed the test reproduces the bug
    assert "diagnosis may be wrong" in _detail(result.gates[1].detail, "reason")


async def test_g6_catches_a_regression_in_a_test_the_diff_never_touched(
    orchestrator: SandboxOrchestrator,
) -> None:
    original = "def calc(a, b):\n    return a + b\n"
    breaking_diff = "--- a/checkout.py\n+++ b/checkout.py\n@@ -1,2 +1,2 @@\n-def calc(a, b):\n-    return a + b\n+def calc(a, b):\n+    return a - b\n"
    g0, files_patched = check_diff_applies(breaking_diff, {"checkout.py": original})
    assert g0.passed
    assert files_patched is not None

    existing_test = (
        "from checkout import calc\n\ndef test_calc_adds():\n    assert calc(2, 3) == 5\n"
    )
    bundle = _bundle(
        files_original={"checkout.py": original},
        files_patched=files_patched,
        existing_tests={"tests/test_calc.py": existing_test},
        gates=("G6",),
    )
    result = await orchestrator.run(bundle)

    assert not result.passed
    assert result.failed_gate == "G6"
    assert result.gates[0].detail["newly_failing"] == ["tests/test_calc.py::test_calc_adds"]


async def test_g6_does_not_count_a_pre_existing_failure_against_the_patch(
    orchestrator: SandboxOrchestrator,
) -> None:
    """`15` T6.4's own accept bar: "Pre-existing test failures are
    classified `already_failing` and don't count against the patch.\""""
    original = "def calc(a, b):\n    return a + b\n"
    # `checkout.py` is genuinely untouched by this patch — files_original
    # and files_patched are identical, same as a real diff that modifies
    # some other file entirely would leave it.
    files_patched = {"checkout.py": original}

    already_broken_test = "from checkout import calc\n\ndef test_calc_already_broken():\n    assert calc(2, 3) == 999\n"
    bundle = _bundle(
        files_original={"checkout.py": original},
        files_patched=files_patched,
        existing_tests={"tests/test_calc.py": already_broken_test},
        gates=("G6",),
    )
    result = await orchestrator.run(bundle)

    assert result.passed
    assert result.gates[0].detail["newly_failing"] == []
    assert result.gates[0].detail["already_failing"] == [
        "tests/test_calc.py::test_calc_already_broken"
    ]


async def test_g7_counts_only_new_static_findings(orchestrator: SandboxOrchestrator) -> None:
    original = "def f():\n    return 1\n"
    diff = "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,3 @@\n+import os\n def f():\n     return 1\n"
    g0, files_patched = check_diff_applies(diff, {"mod.py": original})
    assert g0.passed
    assert files_patched is not None

    bundle = _bundle(
        files_original={"mod.py": original}, files_patched=files_patched, gates=("G7",)
    )
    result = await orchestrator.run(bundle)

    assert result.passed  # an unused import is MEDIUM, not HIGH — doesn't fail the gate
    assert _detail(result.gates[0].detail, "new_medium") >= 1
    assert result.gates[0].detail["new_high"] == 0


async def test_g8_blocks_a_newly_added_dangerous_construct(
    orchestrator: SandboxOrchestrator,
) -> None:
    original = "def f(x):\n    return x\n"
    diff = "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f(x):\n-    return x\n+    return eval(x)\n"
    g0, files_patched = check_diff_applies(diff, {"mod.py": original})
    assert g0.passed
    assert files_patched is not None

    bundle = _bundle(
        files_original={"mod.py": original}, files_patched=files_patched, gates=("G8",)
    )
    result = await orchestrator.run(bundle)

    assert not result.passed
    assert result.failed_gate == "G8"
    findings = _detail(result.gates[0].detail, "findings")
    assert any(f["severity"] == "high" for f in findings)


async def test_g2_installs_real_dependencies_offline(orchestrator: SandboxOrchestrator) -> None:
    original = "def f():\n    return 1\n"
    diff = "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
    g0, files_patched = check_diff_applies(diff, {"mod.py": original})
    assert g0.passed
    assert files_patched is not None

    bundle = _bundle(
        files_original={"mod.py": original},
        files_patched=files_patched,
        manifest={"path": "requirements.txt", "content": "fastapi==0.111.0\nhttpx==0.27.0\n"},
        gates=("G2",),
        budgets={"total_s": 45, "deps_s": 15, "tests_s": 20, "static_s": 10},
    )
    result = await orchestrator.run(bundle)

    assert result.passed
    assert result.mode == "full"
    assert result.gates[0].detail == {"resolved": True, "mode": "full"}


async def test_g2_degrades_to_partial_when_a_non_core_package_is_missing(
    orchestrator: SandboxOrchestrator,
) -> None:
    """`15` T6.5's accept bar: "Removing a required wheel produces `mode:
    'partial'`, skipped test gates, and a capped validation component —
    never a silent pass." `mod.py` never imports the missing package, so
    the source itself still imports fine — `07` §5's "missing non-test
    deps only" case."""
    original = "def f():\n    return 1\n"
    diff = "--- a/mod.py\n+++ b/mod.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2\n"
    g0, files_patched = check_diff_applies(diff, {"mod.py": original})
    assert g0.passed
    assert files_patched is not None

    bundle = _bundle(
        files_original={"mod.py": original},
        files_patched=files_patched,
        manifest={
            "path": "requirements.txt",
            "content": "this-package-does-not-exist-xyz==1.0.0\n",
        },
        new_files={"tests/test_regression.py": "def test_x():\n    assert True\n"},
        regression_test={
            "path": "tests/test_regression.py",
            "test_id": "tests/test_regression.py::test_x",
            "expected_pre": "fail",
            "expected_post": "pass",
        },
        gates=("G2", "G3", "G4", "G5", "G6", "G7", "G8"),
    )
    result = await orchestrator.run(bundle)

    assert result.passed  # a cache miss never fails the whole validation — `07` §5
    assert result.mode == "partial"
    assert result.failed_gate is None

    by_gate = {g.gate: g for g in result.gates}
    assert _detail(by_gate["G2"].detail, "mode") == "partial"
    assert "this-package-does-not-exist-xyz==1.0.0" in _detail(
        by_gate["G2"].detail, "missing_packages"
    )
    assert "degraded_skip" not in by_gate["G3"].detail  # source still imports — G3 runs for real
    assert "degraded_skip" not in by_gate["G7"].detail
    assert "degraded_skip" not in by_gate["G8"].detail
    for skipped in ("G4", "G5", "G6"):
        assert by_gate[skipped].detail["degraded_skip"] is True
        assert by_gate[skipped].passed  # a skip, never a fabricated pass on a real check

    assert result.signals_for_scoring.degraded_mode is True
    assert result.signals_for_scoring.regression_test_valid is None
    assert result.signals_for_scoring.test_pass_ratio is None
    assert result.signals_for_scoring.validation_component_cap == 0.55
    assert result.signals_for_scoring.band_cap is None


async def test_g2_degrades_to_syntax_only_when_a_core_import_is_missing(
    orchestrator: SandboxOrchestrator,
) -> None:
    """`07` §5's "missing core deps" case: the source itself imports the
    missing package, so not even G3's import check can pass — only G7
    (pure static analysis, no import required) still runs for real."""
    original = "import this_package_does_not_exist_xyz\ndef f():\n    return 1\n"
    diff = (
        "--- a/mod.py\n+++ b/mod.py\n@@ -1,3 +1,3 @@\n"
        " import this_package_does_not_exist_xyz\n"
        " def f():\n"
        "-    return 1\n"
        "+    return 2\n"
    )
    g0, files_patched = check_diff_applies(diff, {"mod.py": original})
    assert g0.passed
    assert files_patched is not None

    bundle = _bundle(
        files_original={"mod.py": original},
        files_patched=files_patched,
        manifest={
            "path": "requirements.txt",
            "content": "this-package-does-not-exist-xyz==1.0.0\n",
        },
        gates=("G2", "G3", "G4", "G7"),
    )
    result = await orchestrator.run(bundle)

    assert result.passed  # still not a blocking failure — cache miss, not a defect
    assert result.mode == "syntax_only"
    assert result.failed_gate is None

    by_gate = {g.gate: g for g in result.gates}
    assert _detail(by_gate["G2"].detail, "mode") == "syntax_only"
    assert "mod.py" in _detail(by_gate["G2"].detail, "core_import_errors")
    for skipped in ("G3", "G4"):
        assert by_gate[skipped].detail["degraded_skip"] is True
    assert "degraded_skip" not in by_gate["G7"].detail

    assert result.signals_for_scoring.degraded_mode is True
    assert result.signals_for_scoring.build_passed is False  # G3 never ran to prove it did
    assert result.signals_for_scoring.validation_component_cap == 0.35
    assert result.signals_for_scoring.band_cap == "low"


async def test_fail_fast_stops_at_the_first_failing_gate(orchestrator: SandboxOrchestrator) -> None:
    """`07` §6's ordering is fail-fast — G8 must never run (and its result
    must never appear) once G4 has already failed."""
    g0, files_patched = check_diff_applies(REAL_FIX_DIFF, {"checkout.py": ORIGINAL_CHECKOUT})
    assert g0.passed
    assert files_patched is not None

    theatrical_test = (
        "from checkout import calculate_total\n\ndef test_x():\n"
        "    assert calculate_total(100, 10) == 110\n"  # passes even pre-patch
    )
    bundle = _bundle(
        files_patched=files_patched,
        new_files={"tests/test_regression.py": theatrical_test},
        regression_test={
            "path": "tests/test_regression.py",
            "test_id": "tests/test_regression.py::test_x",
            "expected_pre": "fail",
            "expected_post": "pass",
        },
        gates=("G4", "G5", "G6", "G7", "G8"),
    )
    result = await orchestrator.run(bundle)

    assert result.failed_gate == "G4"
    assert [g.gate for g in result.gates] == ["G4"]
