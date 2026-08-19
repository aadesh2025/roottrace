"""G0/G1 (T6.4, `03` §S8) — the two pre-container gates. No Docker needed;
these run in-process before a container ever exists."""

from __future__ import annotations

import pytest

from roottrace_worker.pipeline.validate.gates import check_diff_applies, check_syntax

pytestmark = pytest.mark.unit


def test_g0_passes_and_returns_patched_content_for_a_clean_diff() -> None:
    diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"
    result, files_patched = check_diff_applies(diff, {"foo.py": "x = 1\n"})

    assert result.gate == "G0"
    assert result.passed
    assert result.duration_ms >= 0
    assert files_patched == {"foo.py": "x = 2\n"}


def test_g0_fails_when_a_hunk_does_not_match() -> None:
    diff = "--- a/foo.py\n+++ b/foo.py\n@@ -1,1 +1,1 @@\n-x = 999\n+x = 2\n"
    result, files_patched = check_diff_applies(diff, {"foo.py": "x = 1\n"})

    assert result.gate == "G0"
    assert not result.passed
    assert files_patched is None
    assert "reason" in result.detail


def test_g0_fails_on_a_malformed_diff() -> None:
    result, files_patched = check_diff_applies("not a diff", {})
    assert not result.passed
    assert files_patched is None


def test_g1_passes_for_syntactically_valid_python() -> None:
    result = check_syntax({"a.py": "def f():\n    return 1\n"})
    assert result.gate == "G1"
    assert result.passed
    assert result.detail["files_parsed"] == 1


def test_g1_fails_for_a_syntax_error() -> None:
    result = check_syntax({"a.py": "def f(:\n    return 1\n"})
    assert not result.passed
    errors = result.detail["errors"]
    assert isinstance(errors, dict)
    assert "a.py" in errors


def test_g1_only_checks_python_files() -> None:
    result = check_syntax({"data.json": "{not json or python, doesn't matter}"})
    assert result.passed
    assert result.detail["files_parsed"] == 0


def test_g1_reports_every_failing_file_not_just_the_first() -> None:
    result = check_syntax(
        {
            "a.py": "def f(:\n",
            "b.py": "def g():\n    return 1\n",
            "c.py": "class C(:\n",
        }
    )
    assert not result.passed
    errors = result.detail["errors"]
    assert isinstance(errors, dict)
    assert set(errors) == {"a.py", "c.py"}
