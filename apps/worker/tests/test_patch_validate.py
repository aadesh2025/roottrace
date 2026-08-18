"""`validate.py` (T5.4) — the deterministic post-validators from `03` §S7's
"Constraints enforced on the output" table, exercised directly against
`PatchReply` without a gateway or model in the loop."""

from __future__ import annotations

import pytest

from roottrace_worker.pipeline.patch.extraction_schema import PatchReply
from roottrace_worker.pipeline.patch.validate import validate_patch
from roottrace_worker.pipeline.reason.contracts import FixStrategy, RootCause, RootCauseAnalysis
from roottrace_worker.pipeline.retrieve.bundle import (
    BundleFile,
    BundleGraph,
    BundleHistory,
    BundleTestMatch,
    BundleTests,
    ContextBundle,
    Quality,
    QualitySignals,
    RepositoryRef,
)

pytestmark = pytest.mark.unit

FILE_CONTENT = (
    "def calculate_total():\n    subtotal = base_price + tax_amount\n    return subtotal\n"
)
TEST_CONTENT = "def test_calculate_total():\n    assert calculate_total() == 1\n"


def _bundle(*, with_test: bool = False) -> ContextBundle:
    return ContextBundle(
        bundle_id="ctx_1",
        repository=RepositoryRef(full_name="acme/checkout-api", ref="main"),
        token_count=100,
        token_budget=24_000,
        files=(
            BundleFile(
                repo_path="services/checkout.py",
                strategy="frame_direct",
                relevance=1.0,
                language="python",
                content=FILE_CONTENT,
                line_range=(1, 3),
                truncated=False,
            ),
        ),
        graph=BundleGraph(),
        history=BundleHistory(),
        tests=BundleTests(
            found=(
                (
                    BundleTestMatch(
                        repo_path="tests/test_checkout.py", covers=(), content=TEST_CONTENT
                    ),
                )
                if with_test
                else ()
            )
        ),
        strategy_stats={},
        quality=Quality(
            score=0.5,
            signals=QualitySignals(
                failure_point_resolved=True,
                entry_point_resolved=True,
                callees_resolved=0,
                callers_resolved=0,
                has_tests=with_test,
                has_release_correlation=False,
            ),
        ),
    )


def _analysis(
    *,
    files_to_modify: tuple[str, ...] = ("services/checkout.py",),
    must_not_modify: tuple[str, ...] = (),
    regression_test_needed: bool = False,
) -> RootCauseAnalysis:
    return RootCauseAnalysis(
        root_cause=RootCause(summary="s", mechanism="m", category="other"),
        reasoning_chain=(),
        fix_strategy=FixStrategy(
            approach="a",
            files_to_modify=files_to_modify,
            must_not_modify=must_not_modify,
            regression_test_needed=regression_test_needed,
        ),
        self_assessed_confidence=0.8,
        model="m",
        prompt_version="patch/v4",
    )


def _reply(diff: str, **overrides: object) -> PatchReply:
    payload: dict[str, object] = {
        "diff": diff,
        "explanation": "x",
        "regression_test": None,
        "risk_assessment": {"level": "low"},
        "alternatives_considered": [],
    }
    payload.update(overrides)
    return PatchReply.model_validate(payload)


VALID_DIFF = (
    "--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -1,3 +1,3 @@\n"
    " def calculate_total():\n-    subtotal = base_price + tax_amount\n"
    "+    subtotal = base_price + (tax_amount or 0)\n     return subtotal\n"
)


def test_a_clean_in_scope_diff_passes() -> None:
    validation = validate_patch(_reply(VALID_DIFF), bundle=_bundle(), analysis=_analysis())
    assert validation.ok
    assert validation.scope_warning is None
    assert validation.manifest_warning is None


def test_a_diff_that_fails_to_parse_is_an_applicability_failure() -> None:
    validation = validate_patch(_reply("not a diff"), bundle=_bundle(), analysis=_analysis())
    assert not validation.ok
    assert validation.applicability_failure is not None
    assert validation.scope_violation is None


def test_a_forbidden_path_is_a_scope_violation() -> None:
    diff = (
        "--- a/.github/workflows/ci.yml\n+++ b/.github/workflows/ci.yml\n@@ -1,1 +1,1 @@\n-x\n+y\n"
    )
    analysis = _analysis(files_to_modify=(".github/workflows/ci.yml",))
    validation = validate_patch(_reply(diff), bundle=_bundle(), analysis=analysis)
    assert not validation.ok
    assert validation.scope_violation is not None
    assert "forbidden path" in validation.scope_violation.reason


def test_a_file_outside_files_to_modify_is_a_scope_violation() -> None:
    diff = "--- a/other/file.py\n+++ b/other/file.py\n@@ -1,1 +1,1 @@\n-x\n+y\n"
    validation = validate_patch(_reply(diff), bundle=_bundle(), analysis=_analysis())
    assert not validation.ok
    assert validation.scope_violation is not None
    assert "outside fix_strategy.files_to_modify" in validation.scope_violation.reason


def test_a_file_in_must_not_modify_is_a_scope_violation_even_if_no_files_to_modify_conflict() -> (
    None
):
    analysis = _analysis(
        files_to_modify=("services/checkout.py",), must_not_modify=("services/checkout.py",)
    )
    validation = validate_patch(_reply(VALID_DIFF), bundle=_bundle(), analysis=analysis)
    assert not validation.ok
    assert validation.scope_violation is not None
    assert "must_not_modify" in validation.scope_violation.reason


def test_the_declared_regression_test_path_is_allowed_even_though_not_in_files_to_modify() -> None:
    diff = (
        "--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1,2 @@\n"
        "+def test_x():\n+    assert True\n"
    )
    reply = _reply(
        diff,
        regression_test={
            "repo_path": "tests/test_new.py",
            "test_name": "test_x",
            "reproduces_original_error": True,
            "expected_before_patch": "fail",
            "expected_after_patch": "pass",
        },
    )
    validation = validate_patch(reply, bundle=_bundle(), analysis=_analysis(files_to_modify=()))
    assert validation.ok


def test_deleting_an_entire_existing_test_file_is_a_scope_violation() -> None:
    diff = (
        "--- a/tests/test_checkout.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n"
        "-def test_calculate_total():\n-    assert calculate_total() == 1\n"
    )
    analysis = _analysis(files_to_modify=("tests/test_checkout.py",))
    validation = validate_patch(_reply(diff), bundle=_bundle(with_test=True), analysis=analysis)
    assert not validation.ok
    assert validation.scope_violation is not None
    assert "deletes it" in validation.scope_violation.reason


def test_deleting_an_existing_test_function_is_a_scope_violation() -> None:
    diff = (
        "--- a/tests/test_checkout.py\n+++ b/tests/test_checkout.py\n@@ -1,2 +1,0 @@\n"
        "-def test_calculate_total():\n-    assert calculate_total() == 1\n"
    )
    analysis = _analysis(files_to_modify=("tests/test_checkout.py",))
    validation = validate_patch(_reply(diff), bundle=_bundle(with_test=True), analysis=analysis)
    assert not validation.ok
    assert validation.scope_violation is not None
    assert "removed without replacement" in validation.scope_violation.reason


def test_replacing_a_test_body_without_removing_its_name_is_not_a_deletion() -> None:
    diff = (
        "--- a/tests/test_checkout.py\n+++ b/tests/test_checkout.py\n@@ -1,2 +1,2 @@\n"
        " def test_calculate_total():\n-    assert calculate_total() == 1\n"
        "+    assert calculate_total() == 2\n"
    )
    analysis = _analysis(files_to_modify=("tests/test_checkout.py",))
    validation = validate_patch(_reply(diff), bundle=_bundle(with_test=True), analysis=analysis)
    assert validation.ok


def test_a_hunk_that_does_not_apply_is_an_applicability_failure_not_a_scope_violation() -> None:
    diff = (
        "--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -1,3 +1,3 @@\n"
        " def calculate_total():\n-    never in the file\n"
        "+    subtotal = base_price + (tax_amount or 0)\n     return subtotal\n"
    )
    validation = validate_patch(_reply(diff), bundle=_bundle(), analysis=_analysis())
    assert not validation.ok
    assert validation.applicability_failure is not None
    assert validation.scope_violation is None


def test_a_required_regression_test_that_is_missing_is_an_applicability_failure() -> None:
    analysis = _analysis(regression_test_needed=True)
    validation = validate_patch(_reply(VALID_DIFF), bundle=_bundle(), analysis=analysis)
    assert not validation.ok
    assert validation.applicability_failure is not None
    assert "regression_test_needed" in validation.applicability_failure.reason


def test_a_present_regression_test_satisfies_the_requirement() -> None:
    analysis = _analysis(regression_test_needed=True)
    reply = _reply(
        VALID_DIFF,
        regression_test={
            "repo_path": "tests/test_checkout.py",
            "test_name": "test_calculate_total_guards_none",
            "reproduces_original_error": True,
            "expected_before_patch": "fail",
            "expected_after_patch": "pass",
        },
    )
    validation = validate_patch(reply, bundle=_bundle(), analysis=analysis)
    assert validation.ok


def test_a_large_diff_sets_scope_warning_but_still_passes() -> None:
    # A brand-new file sidesteps window-matching entirely (`_apply_new_file`
    # only checks that no hunk declares existing source content), which
    # keeps this test focused purely on the >60-line heuristic.
    added_lines = "\n".join(f"+line_{i} = {i}" for i in range(70))
    diff = f"--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1,70 @@\n{added_lines}\n"
    analysis = _analysis(files_to_modify=())
    reply = _reply(
        diff,
        regression_test={
            "repo_path": "tests/test_new.py",
            "test_name": "test_new",
            "reproduces_original_error": True,
            "expected_before_patch": "fail",
            "expected_after_patch": "pass",
        },
    )
    validation = validate_patch(reply, bundle=_bundle(), analysis=analysis)
    assert validation.ok
    assert validation.scope_warning is not None
    assert "exceeds" in validation.scope_warning


def test_touching_a_dependency_manifest_sets_a_manifest_warning_but_still_passes() -> None:
    diff = "--- /dev/null\n+++ b/pyproject.toml\n@@ -0,0 +1,1 @@\n+a = 2\n"
    analysis = _analysis(files_to_modify=("pyproject.toml",))
    validation = validate_patch(_reply(diff), bundle=_bundle(), analysis=analysis)
    assert validation.ok
    assert validation.manifest_warning is not None
    assert "pyproject.toml" in validation.manifest_warning
