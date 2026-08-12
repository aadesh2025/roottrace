"""T3.2 acceptance — the error corpus (`docs/15` §5, `A1`, `14` §6.2).

Two criteria: every payload validates against the ingest schema, and every
ground truth references real symbols at real line numbers in the synthetic
repository.

The second is what `make fixtures-verify` exists for. Ground truth that drifts
from the code does not fail loudly — it quietly changes what the evaluation
harness is measuring, and every score after that is against a moving target.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from fixtures.triggers.cases import CASE_IDS

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REPO = REPO_ROOT / "fixtures" / "synthetic-repo"
CORPUS = REPO_ROOT / "fixtures" / "error-corpus"

CONTROLS = ("unfixable-01", "unfixable-02")

#: `14` §6.1. The corpus must keep this shape, or the metrics computed over it
#: stop meaning what `14` §6.3 says they mean.
EXPECTED_DISTRIBUTION = {
    "null_propagation": 4,
    "type_mismatch": 3,
    "missing_key": 3,
    "external_dependency": 3,
    "race_condition": 2,
    "off_by_one": 2,
    "configuration": 2,
    "regression": 3,
    "resource_leak": 1,
    "unfixable_external": 2,
}


def case(case_id: str) -> dict[str, Any]:
    return json.loads((CORPUS / f"{case_id}.case.json").read_text(encoding="utf-8"))


def payload(case_id: str) -> dict[str, Any]:
    return json.loads((CORPUS / f"{case_id}.json").read_text(encoding="utf-8"))


def _defines(rel_path: str, name: str) -> tuple[int, int] | None:
    """The real line span of a function or method, from the AST."""
    tree = ast.parse((FIXTURE_REPO / rel_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node.lineno, node.end_lineno or node.lineno
    return None


# ── One case, one file ─────────────────────────────────────────────────────


def test_every_case_has_exactly_one_definition() -> None:
    """`14` §6.2. A second definition anywhere is how two documents start
    disagreeing about the same fixture."""
    defined = {path.name.removesuffix(".case.json") for path in CORPUS.glob("*.case.json")}
    assert defined == set(CASE_IDS)


def test_every_case_has_a_payload() -> None:
    for case_id in CASE_IDS:
        assert (CORPUS / f"{case_id}.json").exists(), case_id


def test_the_corpus_matches_the_documented_distribution() -> None:
    counts: dict[str, int] = {}
    for case_id in CASE_IDS:
        counts[case(case_id)["category"]] = counts.get(case(case_id)["category"], 0) + 1
    assert counts == EXPECTED_DISTRIBUTION


def test_exactly_two_controls() -> None:
    controls = [c for c in CASE_IDS if case(c)["difficulty"] == "control"]
    assert sorted(controls) == sorted(CONTROLS)


# ── Payloads validate against the ingest schema (`03` §S1) ─────────────────


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_payload_has_the_required_ingest_fields(case_id: str) -> None:
    body = payload(case_id)
    assert list(body) == ["events"]
    assert len(body["events"]) == 1

    event = body["events"][0]
    for field in ("timestamp", "environment", "service", "level", "error"):
        assert field in event, f"{case_id} is missing {field}"

    assert event["environment"] in ("production", "staging", "development")
    assert event["level"] in ("error", "fatal", "warning")
    assert event["timestamp"].endswith("Z")

    error = event["error"]
    assert error["type"], "error.type is required (RT-INGEST-0011)"
    assert error["message"]
    assert error["stack_trace"].startswith("Traceback (most recent call last):")


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_payload_respects_the_field_size_caps(case_id: str) -> None:
    """`03` §S1: message 8 KB, stack_trace 64 KB. A fixture that exceeded a cap
    would be truncated at ingest, so the corpus would be testing the truncation
    path rather than the case."""
    error = payload(case_id)["events"][0]["error"]
    assert len(error["message"].encode()) <= 8 * 1024
    assert len(error["stack_trace"].encode()) <= 64 * 1024


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_payload_carries_no_authorization_header(case_id: str) -> None:
    """`03` §S1 header allowlist: `Authorization` and `Cookie` are never
    stored under any circumstances, so a fixture must not contain one."""
    headers = payload(case_id)["events"][0].get("request", {}).get("headers", {})
    lowered = {key.lower() for key in headers}
    assert "authorization" not in lowered
    assert "cookie" not in lowered


# ── Frames point at real code ──────────────────────────────────────────────


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_in_app_frame_resolves_to_real_code(case_id: str) -> None:
    """The frames were captured from a real traceback, so this is a guard
    against the repo moving underneath a committed payload rather than against
    the generator."""
    for frame in payload(case_id)["events"][0]["error"]["stack_frames"]:
        if not frame.get("in_app"):
            continue
        relative = _strip_prefix(frame["file"])
        target = FIXTURE_REPO / relative
        assert target.exists(), f"{case_id}: {frame['file']} does not resolve to {relative}"

        lines = target.read_text(encoding="utf-8").splitlines()
        assert 1 <= frame["line"] <= len(lines), f"{case_id}: line {frame['line']} past EOF"
        if "context_line" in frame:
            assert lines[frame["line"] - 1] == frame["context_line"], (
                f"{case_id}: the source at {relative}:{frame['line']} has changed since the "
                "payload was generated — regenerate with `uv run python -m fixtures.corpus.generate`"
            )


def _strip_prefix(reported: str) -> str:
    """Undo the deployment path prefix, as S5's resolution cascade must."""
    normalised = reported.replace("\\", "/")
    for prefix in ("/app/", "/usr/src/app/", "/workspace/services/", "C:/build/app/"):
        candidate = prefix.replace("\\", "/")
        if normalised.startswith(candidate):
            return normalised[len(candidate) :]
    return normalised


def test_three_cases_use_non_standard_path_prefixes() -> None:
    """`A1` §7. Without these, the resolution cascade's heuristic and
    suffix-matching branches are never exercised and only the trivial branch is
    measured."""
    prefixes = set()
    for case_id in CASE_IDS:
        for frame in payload(case_id)["events"][0]["error"]["stack_frames"]:
            reported = frame["file"].replace("\\", "/")
            if not reported.startswith("/app/"):
                prefixes.add(reported.split("/")[1] if reported.startswith("/") else "windows")
    assert len(prefixes) >= 3, prefixes


# ── Ground truth resolves to real symbols ──────────────────────────────────


@pytest.mark.parametrize("case_id", [c for c in CASE_IDS if c not in CONTROLS])
def test_the_root_cause_function_exists_at_the_stated_lines(case_id: str) -> None:
    """The T3.2 acceptance criterion, and the reason `make fixtures-verify`
    runs in CI."""
    expected = case(case_id)["expected"]
    rel_path = expected["root_cause_file"]
    function = expected["root_cause_function"]

    assert (FIXTURE_REPO / rel_path).exists(), rel_path
    span = _defines(rel_path, function)
    assert span is not None, f"{case_id}: {rel_path}::{function} does not exist"

    low, high = expected["root_cause_line_range"]
    assert [low, high] == list(span), (
        f"{case_id}: ground truth says {rel_path}::{function} is at {low}-{high}, "
        f"the code says {span[0]}-{span[1]}"
    )


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_every_referenced_file_exists(case_id: str) -> None:
    expected = case(case_id)["expected"]
    referenced: list[str] = list(expected.get("relevant_files", []))
    for key in ("must_modify_files", "may_modify_files", "must_not_modify_files"):
        referenced += [p for p in expected.get(key, []) if not p.startswith(".github")]

    for rel_path in referenced:
        if rel_path == "requirements.txt":
            continue
        assert (FIXTURE_REPO / rel_path).exists(), f"{case_id} references missing {rel_path}"


@pytest.mark.parametrize("case_id", [c for c in CASE_IDS if c not in CONTROLS])
def test_evidence_citations_resolve(case_id: str) -> None:
    """H1/H2 compare the model's citation against these literally. A citation
    that points past the end of a file cannot be matched by anything."""
    expected = case(case_id)["expected"]
    breadcrumbs = payload(case_id)["events"][0]["breadcrumbs"]

    for citation in expected["evidence_must_cite"]:
        if citation["kind"] == "file":
            target = FIXTURE_REPO / citation["repo_path"]
            assert target.exists(), citation
            line_count = len(target.read_text(encoding="utf-8").splitlines())
            low, high = citation["line_range"]
            assert 1 <= low <= high <= line_count, citation
        elif citation["kind"] == "breadcrumb":
            assert 0 <= citation["index"] < len(breadcrumbs), (
                f"{case_id} cites breadcrumb {citation['index']} of {len(breadcrumbs)}"
            )


@pytest.mark.parametrize("case_id", [c for c in CASE_IDS if c not in CONTROLS])
def test_scope_lists_do_not_overlap(case_id: str) -> None:
    """A file in both `must_modify` and `must_not_modify` makes the case
    unsatisfiable, and H6 would reject every patch for it."""
    expected = case(case_id)["expected"]
    must = set(expected["must_modify_files"])
    may = set(expected["may_modify_files"])
    forbidden = set(expected["must_not_modify_files"])

    assert not must & forbidden, must & forbidden
    assert not may & forbidden, may & forbidden


@pytest.mark.parametrize("case_id", [c for c in CASE_IDS if c not in CONTROLS])
def test_the_root_cause_file_is_modifiable(case_id: str) -> None:
    """If the file holding the defect is not in `must_modify`, the case asks
    for a fix somewhere other than where the bug is."""
    expected = case(case_id)["expected"]
    assert expected["root_cause_file"] in set(expected["must_modify_files"]) | set(
        expected["may_modify_files"]
    )


@pytest.mark.parametrize("case_id", [c for c in CASE_IDS if c not in CONTROLS])
def test_the_reported_error_type_matches_the_payload(case_id: str) -> None:
    """Ground truth and payload must agree about what the tenant saw."""
    assert (
        case(case_id)["expected"]["issue_error_type"]
        == (payload(case_id)["events"][0]["error"]["type"])
    )


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_case_points_at_its_own_payload(case_id: str) -> None:
    assert case(case_id)["api_event"] == f"fixtures/error-corpus/{case_id}.json"


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_introducing_commit_exists_in_the_fixture_history(case_id: str) -> None:
    """ "Introduced by" is one of the strongest signals we have, and it is only
    a signal if the commit is in the simulated history."""
    expected = case(case_id)["expected"]
    commit = expected.get("introduced_by_commit")
    if commit is None:
        return

    metadata = json.loads((FIXTURE_REPO / ".roottrace-fixture.json").read_text(encoding="utf-8"))
    known = {entry["sha"][:8] for entry in metadata["commits"]}
    assert commit in known, f"{case_id} attributes the defect to unknown commit {commit}"


# ── The controls invert (`14` §6.2) ────────────────────────────────────────


@pytest.mark.parametrize("case_id", CONTROLS)
def test_a_control_asserts_what_must_not_happen(case_id: str) -> None:
    expected = case(case_id)["expected"]

    assert expected["final_status"] == "insufficient_context"
    assert expected["should_open_pr"] is False
    assert expected["must_produce_patch"] is False
    assert expected["must_not_fabricate_root_cause"] is True
    assert expected["explanation_must_state_external_cause"] is True
    assert expected["confidence_band"] == "insufficient"


@pytest.mark.parametrize("case_id", CONTROLS)
def test_a_control_names_no_root_cause(case_id: str) -> None:
    """A control with a root cause in its ground truth is a control that
    expects a diagnosis, which is the opposite of what M14 measures."""
    expected = case(case_id)["expected"]
    for field in ("root_cause_file", "root_cause_function", "must_modify_files"):
        assert field not in expected, f"{case_id} should not specify {field}"


# ── Fingerprints are deliberately absent ───────────────────────────────────


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_no_payload_carries_a_memory_address(case_id: str) -> None:
    """Default object reprs include an address that changes on every run.

    Left in, the committed corpus churns on every regeneration: every fixture
    a diff, and `make fixtures-verify` comparing against something nothing
    reproduces. It is also noise to a diagnosis — the class name is the part
    that carries meaning.
    """
    body = json.dumps(payload(case_id))
    assert " at 0x" not in body


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_fingerprints_are_left_for_the_implementation(case_id: str) -> None:
    """`fingerprint` is null on every case, and that is deliberate.

    S2's algorithm does not exist until T2.3. A hand-written fingerprint would
    be a number the implementation is then forced to reproduce by coincidence
    — and if it did not, the "ground truth" would be wrong rather than the
    code. T2.3 fills these in from the real algorithm and this test inverts.
    """
    assert case(case_id)["expected"]["fingerprint"] is None
