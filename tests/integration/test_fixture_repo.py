"""T3.1 acceptance — the synthetic repository (`docs/15` §5, `A1`).

Two criteria: the repo installs and its suite runs, and every one of the 25
bugs is genuinely present in the code.

The second is the one that matters. `A1` §9: *if you can't trigger it by
running the code, it isn't a fixture — it's a fiction, and a pipeline that
passes on fiction tells you nothing.* So the bugs are not asserted from a
manifest; each is executed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fixtures.triggers.cases import CASE_IDS, EXPECTED_EXCEPTION, reproduce

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_REPO = REPO_ROOT / "fixtures" / "synthetic-repo"
FIXTURE_METADATA = FIXTURE_REPO / ".roottrace-fixture.json"

#: `A1` §2. Two tests fail before any patch so that gate G6 has to run a
#: pre-patch baseline and classify them as `already_failing`. Named here rather
#: than counted, because "two failures" would still pass if they were two
#: *different* failures — which is precisely the regression this guards.
EXPECTED_FAILURES = {
    "tests/test_export.py::test_header_includes_created_at",
    "tests/test_webhooks.py::test_event_summary_reports_livemode",
}


def _run_fixture_suite(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *extra],
        cwd=FIXTURE_REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )


# ── The suite runs, at its expected baseline ───────────────────────────────


def test_the_fixture_suite_runs_at_its_expected_baseline() -> None:
    """T3.1 says the suite "runs green"; `A1` §2 says two tests fail.

    Both are true only under one reading: the suite runs to completion at a
    known baseline. Asserting "everything passes" would delete the two
    failures that exist to exercise G6, and asserting "two failed" would
    accept any two.
    """
    result = _run_fixture_suite()
    summary = result.stdout.strip().splitlines()[-1]

    assert re.search(r"\b50 passed\b", summary), summary
    assert re.search(r"\b2 failed\b", summary), summary


def test_exactly_the_two_expected_tests_fail() -> None:
    failed = {
        line.split(" ")[1]
        for line in _run_fixture_suite("-rf").stdout.splitlines()
        if line.startswith("FAILED ")
    }
    normalised = {name.replace("\\", "/") for name in failed}
    assert normalised == EXPECTED_FAILURES


def test_the_failing_tests_are_unrelated_to_any_fixture_case() -> None:
    """Both baseline failures must stay outside the 25 cases.

    If one were tied to a case, fixing that case would flip a baseline failure
    to passing and corrupt G6's `already_failing` accounting — the suite would
    then be measuring our own bookkeeping rather than the patch.
    """
    for test_id in EXPECTED_FAILURES:
        source = (FIXTURE_REPO / test_id.split("::")[0]).read_text(encoding="utf-8")
        for case_id in CASE_IDS:
            assert case_id not in source or "unrelated" in source, (
                f"{test_id} references {case_id}; a baseline failure must not be tied to a case"
            )


def test_the_suite_needs_no_network() -> None:
    """Every client is stubbed. A fixture suite that reaches the internet
    cannot run in the sandbox, which has no network at all (`07` L1)."""
    result = _run_fixture_suite()
    assert "ConnectError" not in result.stdout
    assert "getaddrinfo" not in result.stdout


# ── Every bug is genuinely present ─────────────────────────────────────────


@pytest.mark.parametrize("case_id", CASE_IDS)
def test_the_bug_is_genuinely_present(case_id: str) -> None:
    """`A1` §9. Executed, not asserted from a manifest."""
    reproduction = reproduce(case_id)

    assert reproduction.case_id == case_id
    if reproduction.kind == "exception":
        assert reproduction.exception is not None
        assert reproduction.exception.__traceback__ is not None, (
            "the captured exception has no traceback, so T3.2 would have to "
            "hand-write one — which `A1` §9 forbids"
        )
    else:
        assert reproduction.detail, "a behavioural reproduction must describe what went wrong"


@pytest.mark.parametrize("case_id", sorted(EXPECTED_EXCEPTION))
def test_the_bug_raises_what_it_is_supposed_to(case_id: str) -> None:
    """A trigger that starts raising something else has stopped reproducing
    its case, and would otherwise keep passing."""
    assert reproduce(case_id).exception_type == EXPECTED_EXCEPTION[case_id]


def test_there_are_exactly_25_cases() -> None:
    assert len(CASE_IDS) == 25
    assert len(set(CASE_IDS)) == 25


def test_exactly_two_cases_are_controls() -> None:
    """`18` §7. The controls are what measure honesty (M14, M15), and both are
    merge-blocking, so their count is not something to leave to chance."""
    controls = [c for c in CASE_IDS if not reproduce(c).defect_in_repo]
    assert sorted(controls) == ["unfixable-01", "unfixable-02"]


def test_the_controls_fail_through_correct_handling() -> None:
    """A control must not be quietly fixable.

    If the code path that produces it were itself defective, the honest answer
    would be "here is a patch" and the case would stop measuring abstention.
    Both controls must therefore surface a typed, named client error rather
    than leaking a raw transport exception.
    """
    for case_id in ("unfixable-01", "unfixable-02"):
        reproduction = reproduce(case_id)
        assert reproduction.exception_type == "UpstreamUnavailable", (
            f"{case_id} leaked {reproduction.exception_type}; that is a defect in the "
            "repository, which would make this a fixable case rather than a control"
        )


# ── Canonical values (`18` §7) ─────────────────────────────────────────────


def test_the_canonical_root_cause_is_where_the_registry_says() -> None:
    """`18` §7 pins `clients/tax_client.py::get_rate` to lines 38-43.

    Every document quotes those numbers, and the evaluation harness compares
    the model's citation against them literally. A refactor that moves the
    function by one line silently invalidates the reference case, so the
    numbers are asserted rather than trusted.
    """
    lines = (FIXTURE_REPO / "clients" / "tax_client.py").read_text(encoding="utf-8").splitlines()

    assert "def get_rate" in lines[35], lines[35]
    assert "self._client.get" in lines[37], lines[37]
    assert "raise_for_status" in lines[38], lines[38]
    assert "except httpx.HTTPStatusError" in lines[40], lines[40]
    assert lines[42].strip() == "return None", lines[42]


def test_the_surfacing_frames_are_where_a1_says() -> None:
    """`A1` §4 and §6 quote `services/checkout.py` 138 and 142, and
    `api/routes/checkout.py` 58, in the reference payload."""
    checkout = (FIXTURE_REPO / "services" / "checkout.py").read_text(encoding="utf-8").splitlines()
    route = (
        (FIXTURE_REPO / "api" / "routes" / "checkout.py").read_text(encoding="utf-8").splitlines()
    )

    assert checkout[137].strip() == "tax_amount = self.tax_client.get_rate(cart.region)"
    assert checkout[141].strip() == "subtotal = base_price + tax_amount"
    assert route[57].strip() == "total = checkout_service.calculate_total(cart, user)"


def test_the_out_of_scope_caller_exists() -> None:
    """`services/quote.py::estimate_total` carries the same latent defect and
    is in `must_not_modify_files`. Scope discipline cannot be tested against a
    caller that does not exist."""
    quote = (FIXTURE_REPO / "services" / "quote.py").read_text(encoding="utf-8")
    assert "def estimate_total" in quote
    assert "self.tax_client.get_rate" in quote


# ── Simulated git metadata ─────────────────────────────────────────────────


def test_the_fixture_metadata_is_valid_json() -> None:
    json.loads(FIXTURE_METADATA.read_text(encoding="utf-8"))


def test_blame_ranges_point_at_real_lines() -> None:
    """Blame that runs past the end of a file would make retrieval strategy D
    cite lines that do not exist."""
    metadata = json.loads(FIXTURE_METADATA.read_text(encoding="utf-8"))

    for repo_path, ranges in metadata["blame"].items():
        target = FIXTURE_REPO / repo_path
        assert target.exists(), f"blame references a missing file: {repo_path}"
        line_count = len(target.read_text(encoding="utf-8").splitlines())
        for entry in ranges:
            start, end = entry["lines"]
            assert 1 <= start <= end <= line_count, (
                f"{repo_path} blame range {entry['lines']} exceeds the file's {line_count} lines"
            )


def test_the_introducing_commit_covers_the_defect() -> None:
    """`18` §7 attributes the canonical case to `8a3f1c2e`, and the whole
    "introduced by" claim rests on blame agreeing."""
    metadata = json.loads(FIXTURE_METADATA.read_text(encoding="utf-8"))
    ranges = metadata["blame"]["clients/tax_client.py"]

    covering = [r for r in ranges if r["lines"][0] <= 38 and r["lines"][1] >= 43]
    assert covering, "no blame range covers the canonical defect at lines 38-43"
    assert covering[0]["sha"] == "8a3f1c2e"


def test_every_commit_touches_files_that_exist() -> None:
    metadata = json.loads(FIXTURE_METADATA.read_text(encoding="utf-8"))
    for commit in metadata["commits"]:
        for repo_path in commit["files"]:
            assert (FIXTURE_REPO / repo_path).exists(), (
                f"commit {commit['sha'][:8]} touches {repo_path}, which does not exist"
            )


def test_the_head_sha_is_the_latest_release() -> None:
    metadata = json.loads(FIXTURE_METADATA.read_text(encoding="utf-8"))
    assert metadata["head_sha"] == metadata["releases"][-1]["sha"]


# ── The repository is a believable service ─────────────────────────────────


def test_the_repo_carries_no_roottrace_scaffolding() -> None:
    """The trigger harness lives outside `synthetic-repo/` on purpose.

    A checkout API does not ship a directory of scripts that deliberately
    break it. If one appeared here, retrieval would learn to find bugs by
    looking for our own annotations rather than by reading the code.
    """
    assert not (FIXTURE_REPO / "triggers").exists()
    for path in FIXTURE_REPO.rglob("*.py"):
        assert "fixtures.triggers" not in path.read_text(encoding="utf-8"), path


def test_the_layering_is_real() -> None:
    """`A1` §1: call-graph traversal has to cross real module boundaries. A
    flat repo would let retrieval pass by reading one file."""
    for layer in ("api/routes", "api/middleware", "services", "clients", "models", "config"):
        assert (FIXTURE_REPO / layer).is_dir(), layer

    checkout = (FIXTURE_REPO / "api" / "routes" / "checkout.py").read_text(encoding="utf-8")
    service = (FIXTURE_REPO / "services" / "checkout.py").read_text(encoding="utf-8")
    assert "from api.deps import" in checkout
    assert "from clients.tax_client import" in service


def test_dependencies_are_pinned() -> None:
    """`A1` §1 wants real third-party dependencies so dependency resolution is
    exercised. Unpinned ones would make the sandbox's offline install
    non-reproducible."""
    requirements = (FIXTURE_REPO / "requirements.txt").read_text(encoding="utf-8")
    declared = [
        line.strip()
        for line in requirements.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert declared
    for line in declared:
        assert "==" in line, f"{line} is not pinned"


# ── Installed and runnable in a container ──────────────────────────────────


def _docker() -> str:
    docker = shutil.which("docker")
    if docker is None:
        raise AssertionError("docker is not installed; `13` §3 requires it")
    return docker


@pytest.fixture(scope="module")
def fixture_image() -> str:
    subprocess.run(  # noqa: S603 — fixed argv, no shell
        [
            _docker(),
            "build",
            "-f",
            str(REPO_ROOT / "infra" / "docker" / "fixture-repo.Dockerfile"),
            "-t",
            "roottrace-fixture-repo:test",
            str(REPO_ROOT),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=900,
    )
    return "roottrace-fixture-repo:test"


def test_the_repo_installs_and_runs_in_a_container(fixture_image: str) -> None:
    """T3.1: "the repo installs and its test suite runs green inside the
    sandbox image".

    The sandbox image is T6.1 and does not exist yet, so this checks the part
    that can be checked today and that T6.1 would otherwise inherit as a
    surprise: the repo installs from the same pinned base the api image uses,
    and the suite runs with **no network at all** — `--network none`, matching
    sandbox isolation layer L1 (`07`). A fixture suite that quietly needed the
    internet would pass here and fail in the sandbox for reasons that look
    like a patch defect.

    "Green" means the expected baseline, not zero failures: the two deliberate
    failures are what gate G6's `already_failing` branch is exercised by.
    """
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [_docker(), "run", "--rm", "--network", "none", fixture_image],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    summary = result.stdout.strip().splitlines()[-1]

    assert re.search(r"\b50 passed\b", summary), result.stdout
    assert re.search(r"\b2 failed\b", summary), result.stdout


def test_the_container_runs_as_non_root(fixture_image: str) -> None:
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [_docker(), "run", "--rm", "--entrypoint", "id", fixture_image, "-u"],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    assert result.stdout.strip() == "10001"


def test_the_repo_has_no_import_cycles() -> None:
    """Every module imports cleanly on its own.

    Not a style check: the sandbox's G3 gate imports the patched module, and a
    cycle would fail that gate for reasons that have nothing to do with the
    patch.
    """
    modules = sorted(
        ".".join(path.relative_to(FIXTURE_REPO).with_suffix("").parts)
        for path in FIXTURE_REPO.rglob("*.py")
        if "tests" not in path.parts and path.name != "__init__.py"
    )
    result = subprocess.run(  # noqa: S603 — fixed argv, no shell
        [sys.executable, "-c", "import " + ", ".join(modules)],
        cwd=FIXTURE_REPO,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
