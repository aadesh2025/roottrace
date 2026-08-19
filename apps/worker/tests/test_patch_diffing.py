"""`diffing.py` — parsing and in-memory application of a unified diff. T5.4's
half (`apply_diff_to_bundle`) checks a diff against a `ContextBundle`'s
retrieved windows, `03` §S7's "we apply it in-memory with `unidiff` before
accepting". T6.4's half (`apply_diff_to_files`, `03` §S8's G0) applies a
diff against full file content and actually produces the patched text —
nothing before S8 needed to do that, only to check applicability. No
gateway, no model, purely mechanical either way."""

from __future__ import annotations

import pytest

from roottrace_worker.pipeline.patch.diffing import (
    apply_diff_to_bundle,
    apply_diff_to_files,
    parse_diff,
)
from roottrace_worker.pipeline.retrieve.bundle import (
    BundleFile,
    BundleGraph,
    BundleHistory,
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


def _bundle(*, line_range: tuple[int, int] = (1, 3), content: str = FILE_CONTENT) -> ContextBundle:
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
                content=content,
                line_range=line_range,
                truncated=False,
            ),
        ),
        graph=BundleGraph(),
        history=BundleHistory(),
        tests=BundleTests(),
        strategy_stats={},
        quality=Quality(
            score=0.5,
            signals=QualitySignals(
                failure_point_resolved=True,
                entry_point_resolved=True,
                callees_resolved=0,
                callers_resolved=0,
                has_tests=False,
                has_release_correlation=False,
            ),
        ),
    )


def test_a_clean_diff_parses_with_accurate_file_stats() -> None:
    diff_text = (
        "--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -1,3 +1,3 @@\n"
        " def calculate_total():\n-    subtotal = base_price + tax_amount\n"
        "+    subtotal = base_price + (tax_amount or 0)\n     return subtotal\n"
    )
    result = parse_diff(diff_text)
    assert result.ok
    assert len(result.file_stats) == 1
    stat = result.file_stats[0]
    assert stat.repo_path == "services/checkout.py"
    assert stat.additions == 1
    assert stat.deletions == 1
    assert stat.hunks == 1
    assert not stat.is_added_file


def test_a_malformed_diff_with_no_recognisable_headers_fails_to_parse() -> None:
    result = parse_diff("this is not a unified diff at all")
    assert not result.ok
    assert result.error is not None


def test_a_hunk_header_that_disagrees_with_its_own_line_count_raises_a_parse_error() -> None:
    # unidiff raises `UnidiffParseError` outright when a hunk's declared
    # line counts don't match what follows, rather than silently parsing
    # to zero files (the `test_a_malformed_diff...` case above).
    diff_text = (
        "--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -1,99 +1,99 @@\n"
        " def calculate_total():\n"
    )
    result = parse_diff(diff_text)
    assert not result.ok
    assert result.error is not None


def test_a_new_file_hunk_that_declares_existing_source_content_fails_to_apply() -> None:
    # Malformed on purpose: `/dev/null` marks this as a new file, but the
    # hunk carries 3 context lines rather than 3 purely-added ones — a
    # self-consistent hunk (the declared and actual line counts agree, so
    # `unidiff` itself does not reject it) that still makes no sense for a
    # file that does not yet exist.
    diff_text = "--- /dev/null\n+++ b/tests/test_new.py\n@@ -1,3 +1,3 @@\n line1\n line2\n line3\n"
    parsed = parse_diff(diff_text)
    assert parsed.patch_set is not None
    result = apply_diff_to_bundle(parsed.patch_set, bundle=_bundle())
    assert not result.applies_cleanly
    assert "declares" in (result.failure_reason or "")


def test_a_pure_insertion_hunk_in_an_existing_file_applies_cleanly() -> None:
    diff_text = (
        "--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -2,0 +3,1 @@\n"
        "+    # a comment\n"
    )
    parsed = parse_diff(diff_text)
    assert parsed.patch_set is not None
    result = apply_diff_to_bundle(parsed.patch_set, bundle=_bundle())
    assert result.applies_cleanly


def test_a_clean_diff_applies_against_the_retrieved_window() -> None:
    diff_text = (
        "--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -1,3 +1,3 @@\n"
        " def calculate_total():\n-    subtotal = base_price + tax_amount\n"
        "+    subtotal = base_price + (tax_amount or 0)\n     return subtotal\n"
    )
    parsed = parse_diff(diff_text)
    assert parsed.patch_set is not None
    result = apply_diff_to_bundle(parsed.patch_set, bundle=_bundle())
    assert result.applies_cleanly


def test_a_hunk_that_does_not_match_retrieved_content_fails_to_apply() -> None:
    diff_text = (
        "--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -1,3 +1,3 @@\n"
        " def calculate_total():\n-    this line was never in the file\n"
        "+    subtotal = base_price + (tax_amount or 0)\n     return subtotal\n"
    )
    parsed = parse_diff(diff_text)
    assert parsed.patch_set is not None
    result = apply_diff_to_bundle(parsed.patch_set, bundle=_bundle())
    assert not result.applies_cleanly
    assert "does not match" in (result.failure_reason or "")


def test_a_hunk_outside_the_retrieved_window_fails_to_apply() -> None:
    # The bundle only retrieved lines 1-3; a hunk starting at line 40 cannot
    # be verified against anything actually fetched.
    diff_text = (
        "--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -40,3 +40,3 @@\n"
        " def calculate_total():\n-    subtotal = base_price + tax_amount\n"
        "+    subtotal = base_price + (tax_amount or 0)\n     return subtotal\n"
    )
    parsed = parse_diff(diff_text)
    assert parsed.patch_set is not None
    result = apply_diff_to_bundle(parsed.patch_set, bundle=_bundle())
    assert not result.applies_cleanly
    assert "outside the retrieved content" in (result.failure_reason or "")


def test_a_file_absent_from_the_bundle_cannot_be_verified() -> None:
    diff_text = "--- a/unretrieved/other.py\n+++ b/unretrieved/other.py\n@@ -1,1 +1,1 @@\n-a\n+b\n"
    parsed = parse_diff(diff_text)
    assert parsed.patch_set is not None
    result = apply_diff_to_bundle(parsed.patch_set, bundle=_bundle())
    assert not result.applies_cleanly
    assert "not present in the retrieved bundle" in (result.failure_reason or "")


def test_a_genuinely_new_file_applies_without_a_bundle_entry() -> None:
    diff_text = (
        "--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1,2 @@\n"
        "+def test_x():\n+    assert True\n"
    )
    parsed = parse_diff(diff_text)
    assert parsed.patch_set is not None
    result = apply_diff_to_bundle(parsed.patch_set, bundle=_bundle())
    assert result.applies_cleanly


def test_multi_file_diffs_parse_without_diff_git_preambles() -> None:
    """`03` §S7's own worked example uses plain `---`/`+++`/`@@` headers with
    no `diff --git` preamble — the model is never asked to produce one."""
    diff_text = (
        "--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -1,3 +1,3 @@\n"
        " def calculate_total():\n-    subtotal = base_price + tax_amount\n"
        "+    subtotal = base_price + (tax_amount or 0)\n     return subtotal\n"
        "--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1,2 @@\n"
        "+def test_x():\n+    assert True\n"
    )
    parsed = parse_diff(diff_text)
    assert parsed.ok
    assert len(parsed.file_stats) == 2


# ── apply_diff_to_files (T6.4, G0) ───────────────────────────────────────


def test_applies_a_single_hunk_modification_to_full_file_content() -> None:
    diff_text = (
        "--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,4 @@\n"
        " def f():\n-    return 1\n+    return 2\n+    # extra\n def g():\n"
    )
    result = apply_diff_to_files(diff_text, {"foo.py": "def f():\n    return 1\ndef g():\n"})
    assert result.ok
    assert result.files_patched == {"foo.py": "def f():\n    return 2\n    # extra\ndef g():\n"}


def test_applies_multiple_hunks_in_the_same_file() -> None:
    diff_text = (
        "--- a/multi.py\n+++ b/multi.py\n@@ -1,2 +1,2 @@\n"
        "-a = 1\n+a = 100\n b = 2\n@@ -5,2 +5,2 @@\n-e = 5\n+e = 500\n f = 6\n"
    )
    files = {"multi.py": "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\nf = 6\n"}
    result = apply_diff_to_files(diff_text, files)
    assert result.ok
    assert result.files_patched == {"multi.py": "a = 100\nb = 2\nc = 3\nd = 4\ne = 500\nf = 6\n"}


def test_a_new_file_is_added_to_the_result() -> None:
    diff_text = "--- /dev/null\n+++ b/tests/test_new.py\n@@ -0,0 +1,2 @@\n+def test_x():\n+    assert True\n"
    result = apply_diff_to_files(diff_text, {})
    assert result.ok
    assert result.files_patched == {"tests/test_new.py": "def test_x():\n    assert True\n"}


def test_a_deleted_file_is_removed_from_the_result() -> None:
    diff_text = "--- a/old.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-x = 1\n-y = 2\n"
    result = apply_diff_to_files(diff_text, {"old.py": "x = 1\ny = 2\n", "keep.py": "z = 1\n"})
    assert result.ok
    assert result.files_patched == {"keep.py": "z = 1\n"}


def test_an_untouched_file_carries_over_unchanged() -> None:
    diff_text = "--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"
    result = apply_diff_to_files(diff_text, {"a.py": "x = 1\n", "b.py": "y = 1\n"})
    assert result.ok
    assert result.files_patched == {"a.py": "x = 2\n", "b.py": "y = 1\n"}


def test_a_hunk_that_does_not_match_the_file_fails_to_apply() -> None:
    diff_text = (
        "--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,2 @@\n def f():\n-    return 999\n+    return 2\n"
    )
    result = apply_diff_to_files(diff_text, {"foo.py": "def f():\n    return 1\n"})
    assert not result.ok
    assert "does not match" in (result.failure_reason or "")


def test_a_file_referenced_but_absent_from_files_original_fails_to_apply() -> None:
    diff_text = "--- a/missing.py\n+++ b/missing.py\n@@ -1,1 +1,1 @@\n-x = 1\n+x = 2\n"
    result = apply_diff_to_files(diff_text, {})
    assert not result.ok
    assert "not present" in (result.failure_reason or "")


def test_a_malformed_diff_fails_to_apply() -> None:
    result = apply_diff_to_files("not a diff at all", {})
    assert not result.ok
    assert "failed to parse" in (result.failure_reason or "")
