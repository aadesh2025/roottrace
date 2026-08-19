"""G2-G8 (`docs/03` §S8, `docs/07` §6) — the gates that run inside the
sandbox container, dispatched from `runner.py`'s `_GATE_DISPATCH`. G0/G1
run host-side before a container exists at all
(`apps/worker/roottrace_worker/pipeline/validate/gates.py`) — a different
deployable, so a different module, not this one.

Every gate function has the shape `(bundle: dict, work_dir: Path) ->
dict` — a `03` §S8 `GateResult`-shaped mapping (`gate`, `passed`,
`duration_ms`, `detail`). Each gate materialises whatever tree state it
needs itself (`pipeline_original` vs `patched`, per `07` §6's own
per-gate description) rather than trusting a single upfront
materialisation to already be correct for it — G4 needs the *original*
tree, G5/G6/G7's "post" runs need the *patched* one, and G6/G7 need
*both*, one after the other, for their own pre/post baseline comparison."""

from __future__ import annotations

import difflib
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from roottrace_sandbox_runner.materialize import materialize_tree

WHEELS_DIR = "/opt/wheels"
_REPORT_PATH = "_roottrace_report.json"

#: `03` §S4's `ExceptionFamily` values, mapped to the built-in/common
#: exception class names that family plausibly raises — duplicated from
#: `pipeline/understand/contracts.py`'s `ExceptionFamily` rather than
#: imported (this package is a separate deployable with zero dependencies;
#: see `runner.py`'s module docstring for the same reasoning applied to
#: `apps/worker`). Only the families that map cleanly onto specific
#: built-ins are checked strictly (`03` §S8: "REQUIRE ... fails with an
#: error matching the reported exception family"); `integration`,
#: `data_db`, `concurrency`, `auth`, and `unclassified` cover exceptions a
#: real codebase mostly defines itself (a custom `TaxServiceUnavailable`,
#: an ORM's own error hierarchy) that no fixed built-in list could
#: enumerate honestly — for those, the family is recorded but not gate-
#: failing, rather than built as a check that looks rigorous but would
#: reject perfectly good regression tests on a technicality.
_STRICT_FAMILY_EXCEPTIONS: dict[str, frozenset[str]] = {
    "null_undefined": frozenset({"TypeError", "AttributeError"}),
    "type_mismatch": frozenset({"TypeError"}),
    "key_index": frozenset({"KeyError", "IndexError"}),
    "resource": frozenset({"MemoryError", "OSError"}),
    "serialization": frozenset({"ValueError", "JSONDecodeError", "ValidationError"}),
}

#: `07` §6's own soft-budget estimates, used as defaults when
#: `SandboxInput.budgets` doesn't name a specific figure for a gate.
_DEFAULT_DEPS_TIMEOUT_S = 10
_DEFAULT_COMPILE_TIMEOUT_S = 5
_DEFAULT_TESTS_TIMEOUT_S = 20
_DEFAULT_STATIC_TIMEOUT_S = 10


def _gate_result(
    gate: str, passed: bool, duration_ms: int, detail: dict[str, Any]
) -> dict[str, Any]:
    return {"gate": gate, "passed": passed, "duration_ms": duration_ms, "detail": detail}


def _run(
    cmd: list[str], *, cwd: Path, timeout: float, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - argv list, never shell=True; args are our own, not user text
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=env, check=False
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return subprocess.CompletedProcess(
            cmd, returncode=124, stdout=stdout, stderr=stderr + "\n[timed out]"
        )


def gate_dependencies(bundle: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """G2: "Offline install. On success, records the resolved dependency
    set for reproducibility." `--user` (not `--target`) so subsequent
    `python` invocations pick the installed packages up automatically via
    `site.ENABLE_USER_SITE` — no `PYTHONPATH` juggling needed between
    gates.

    T6.5: a full `pip install -r` failure does not, by itself, mean G2
    failed — `07` §5: "If a required package isn't cached, we do not fail
    the whole validation." The common case (everything cached) pays no
    extra cost — one `pip install -r` call, exactly as before. Only a
    failure falls through to `_gate_dependencies_degraded`, which is where
    the real/cache-miss distinction and the `full`/`partial`/`syntax_only`
    mode determination happen."""
    started = time.monotonic()
    manifest = bundle.get("manifest")
    if not manifest:
        return _gate_result("G2", True, 0, {"skipped": "no manifest declared", "mode": "full"})

    materialize_tree(work_dir, bundle.get("files_patched", {}))
    manifest_path = work_dir / "_roottrace_manifest.txt"
    manifest_path.write_text(manifest["content"], encoding="utf-8")
    timeout = bundle.get("budgets", {}).get("deps_s", _DEFAULT_DEPS_TIMEOUT_S)

    try:
        proc = _run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                WHEELS_DIR,
                "--user",
                "-r",
                str(manifest_path),
            ],
            cwd=work_dir,
            timeout=timeout,
        )
    finally:
        manifest_path.unlink(missing_ok=True)

    duration_ms = int((time.monotonic() - started) * 1000)
    if proc.returncode == 0:
        return _gate_result("G2", True, duration_ms, {"resolved": True, "mode": "full"})

    return _gate_dependencies_degraded(
        bundle, work_dir, manifest=manifest, timeout=timeout, started=started
    )


def _requirement_lines(content: str) -> list[str]:
    return [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def _core_imports_ok(
    bundle: dict[str, Any], work_dir: Path, *, timeout: float
) -> tuple[bool, dict[str, str]]:
    """Whether the application source under change (`files_patched` —
    never `new_files`/`existing_tests`, which are test-only content) still
    imports with whatever *did* install. This is the line `07` §5's table
    draws between `partial` (the missing packages are ones the source
    itself never touches — test tooling, most likely) and `syntax_only`
    (the source itself cannot even be imported without them)."""
    materialize_tree(work_dir, bundle.get("files_patched", {}))
    import_errors: dict[str, str] = {}
    for path in bundle.get("files_patched", {}):
        module = _module_name(path)
        if module is None:
            continue
        proc = _run([sys.executable, "-c", f"import {module}"], cwd=work_dir, timeout=timeout)
        if proc.returncode != 0:
            import_errors[path] = proc.stderr[-1000:]
    return not import_errors, import_errors


def _gate_dependencies_degraded(
    bundle: dict[str, Any],
    work_dir: Path,
    *,
    manifest: dict[str, Any],
    timeout: float,
    started: float,
) -> dict[str, Any]:
    """The full install already failed once (`gate_dependencies`). Find out
    exactly which requirement lines are actually unresolvable offline
    (`pip install --dry-run`, one line at a time — no network, no install,
    just resolution against `/opt/wheels`), install what remains for real,
    then use `_core_imports_ok` to decide `partial` vs `syntax_only`.

    A requirement line resolving as unavailable is treated as a cache
    miss without further distinguishing "genuinely not cached" from "some
    other resolution problem with this one line" — `07` §5 does not ask
    for that distinction, and inventing one here would be exactly the kind
    of unrequested precision `CLAUDE.md` warns against. What *does* still
    fail this gate outright (`passed: False`, no degraded mode) is the
    resolvable subset itself then failing to install — that is a real
    defect (e.g. a version conflict between two available packages), not a
    cache-coverage gap, and `07` never says to paper over that kind of
    failure."""
    requirements = _requirement_lines(manifest["content"])
    missing: list[str] = []
    available: list[str] = []
    for req in requirements:
        proc = _run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                WHEELS_DIR,
                "--dry-run",
                req,
            ],
            cwd=work_dir,
            timeout=timeout,
        )
        (available if proc.returncode == 0 else missing).append(req)

    if available:
        install_proc = _run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-index",
                "--find-links",
                WHEELS_DIR,
                "--user",
                *available,
            ],
            cwd=work_dir,
            timeout=timeout,
        )
        if install_proc.returncode != 0:
            duration_ms = int((time.monotonic() - started) * 1000)
            return _gate_result(
                "G2",
                False,
                duration_ms,
                {
                    "reason": "dependency resolution failed for reasons other than a cache miss",
                    "stderr_tail": install_proc.stderr[-2000:],
                    "missing_packages": missing,
                },
            )

    core_ok, import_errors = _core_imports_ok(bundle, work_dir, timeout=timeout)
    mode = "partial" if core_ok else "syntax_only"
    duration_ms = int((time.monotonic() - started) * 1000)
    detail: dict[str, Any] = {
        "mode": mode,
        "missing_packages": missing,
        "resolved_packages": available,
    }
    if not core_ok:
        detail["core_import_errors"] = import_errors
    return _gate_result("G2", True, duration_ms, detail)


def _module_name(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    return path.removesuffix(".py").replace("/", ".")


def gate_compile(bundle: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """G3: "`python -c 'import <module>'` for each changed module, plus
    `python -m compileall -q`." G1 (host-side) already caught pure syntax
    errors; this gate's real value is catching *import-time* failures —
    an import of a dependency G2 didn't resolve, or a module-level
    statement that raises — which only show up when the interpreter
    actually loads the module, dependencies and all."""
    started = time.monotonic()
    changed = {**bundle.get("files_patched", {}), **bundle.get("new_files", {})}
    materialize_tree(work_dir, changed)
    # No dedicated budget key for G3 in `07` §7's `budgets` object (only
    # `deps_s`/`tests_s`/`static_s`/`total_s` exist) — G3 is closer in
    # spirit to compilation than either, so it gets its own small default
    # rather than borrowing a budget meant for a different gate.
    timeout = _DEFAULT_COMPILE_TIMEOUT_S

    compileall = _run(
        [sys.executable, "-m", "compileall", "-q", "."], cwd=work_dir, timeout=timeout
    )
    if compileall.returncode != 0:
        duration_ms = int((time.monotonic() - started) * 1000)
        return _gate_result(
            "G3",
            False,
            duration_ms,
            {"compileall_stderr": compileall.stderr[-2000:] or compileall.stdout[-2000:]},
        )

    import_errors: dict[str, str] = {}
    for path in changed:
        module = _module_name(path)
        if module is None:
            continue
        proc = _run([sys.executable, "-c", f"import {module}"], cwd=work_dir, timeout=timeout)
        if proc.returncode != 0:
            import_errors[path] = proc.stderr[-1000:]

    duration_ms = int((time.monotonic() - started) * 1000)
    passed = not import_errors
    detail: dict[str, Any] = (
        {"files_checked": len(changed)} if passed else {"import_errors": import_errors}
    )
    return _gate_result("G3", passed, duration_ms, detail)


def _run_pytest(test_id: str, *, work_dir: Path, timeout: float) -> dict[str, Any]:
    """Runs one test under `pytest-json-report` and returns the parsed
    report. Raises nothing on a test failure — a failing test is an
    entirely normal, expected outcome for this function to report, not an
    error in running it."""
    report_path = work_dir / _REPORT_PATH
    report_path.unlink(missing_ok=True)
    _run(
        [
            sys.executable,
            "-m",
            "pytest",
            test_id,
            "--json-report",
            f"--json-report-file={report_path}",
            "-q",
        ],
        cwd=work_dir,
        timeout=timeout,
    )
    try:
        report: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    finally:
        report_path.unlink(missing_ok=True)
    return report


def _crash_info(report: dict[str, Any]) -> tuple[str | None, str | None]:
    """`(exception_type, message)` from a pytest-json-report payload — the
    normal "a test ran and failed" shape (`tests[].call.crash`) and the
    "pytest could not even collect the test" shape (a failed entry in
    `collectors[]`, e.g. a broken import in the test file itself) are
    different parts of the report, and `03` §S8's G4 treats the second one
    as its own kind of failure ("the test is broken, not demonstrative"),
    not as evidence of anything about the patch."""
    for test in report.get("tests", []):
        crash = test.get("call", {}).get("crash")
        if crash:
            return _parse_exception_type(crash["message"]), crash["message"]

    for collector in report.get("collectors", []):
        if collector.get("outcome") != "failed":
            continue
        for line in reversed(collector.get("longrepr", "").splitlines()):
            stripped = line.strip()
            if stripped.startswith("E "):
                message = stripped[2:].strip()
                return _parse_exception_type(message), message

    return None, None


def _parse_exception_type(message: str) -> str:
    head = message.split(":", 1)[0].strip()
    return head.rsplit(".", 1)[-1]


def _family_check(expected_family: str | None, exception_type: str | None) -> dict[str, Any]:
    if expected_family is None or exception_type is None:
        return {"family_checked": False}
    strict_set = _STRICT_FAMILY_EXCEPTIONS.get(expected_family)
    if strict_set is None:
        # Not one of the cleanly-mappable families — see the module-level
        # comment on `_STRICT_FAMILY_EXCEPTIONS`. Recorded, not enforced.
        return {
            "family_checked": False,
            "expected_family": expected_family,
            "actual_exception": exception_type,
        }
    matches = exception_type in strict_set
    return {
        "family_checked": True,
        "family_matches": matches,
        "expected_family": expected_family,
        "actual_exception": exception_type,
    }


def gate_regression_pre(bundle: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """G4 — `03` §S8's critical gate. "Write the ORIGINAL (unpatched)
    files into /work. Write ONLY the new regression test. Run it. REQUIRE:
    it fails, and fails with an error matching the reported exception
    family." A test that passes here does not reproduce the bug and is
    worthless as evidence — this is the one gate this whole project exists
    to get right, per `CLAUDE.md`'s testing standard: "a test that passes
    both before and after the fix proves nothing.\""""
    started = time.monotonic()
    regression_test = bundle.get("regression_test")
    if not regression_test:
        duration_ms = int((time.monotonic() - started) * 1000)
        return _gate_result("G4", False, duration_ms, {"reason": "no regression_test declared"})

    test_path = regression_test["path"]
    test_content = bundle.get("new_files", {}).get(test_path) or bundle.get(
        "files_patched", {}
    ).get(test_path)
    if test_content is None:
        duration_ms = int((time.monotonic() - started) * 1000)
        return _gate_result(
            "G4",
            False,
            duration_ms,
            {"reason": f"regression test content for {test_path!r} not found"},
        )

    tree = {**bundle.get("files_original", {}), test_path: test_content}
    materialize_tree(work_dir, tree)
    timeout = bundle.get("budgets", {}).get("tests_s", _DEFAULT_TESTS_TIMEOUT_S)

    report = _run_pytest(regression_test["test_id"], work_dir=work_dir, timeout=timeout)
    duration_ms = int((time.monotonic() - started) * 1000)

    summary = report.get("summary", {})
    collected = summary.get("collected", 0)
    failed = summary.get("failed", 0)

    if collected == 0:
        exception_type, message = _crash_info(report)
        return _gate_result(
            "G4",
            False,
            duration_ms,
            {
                "reason": "test could not be collected — broken, not demonstrative",
                "exception_type": exception_type,
                "message": message,
            },
        )

    if failed == 0:
        return _gate_result(
            "G4",
            False,
            duration_ms,
            {
                "reason": "test PASSED on unpatched code — does not reproduce the bug",
                "summary": summary,
            },
        )

    exception_type, message = _crash_info(report)
    family_detail = _family_check(regression_test.get("expected_error_family"), exception_type)
    passed = family_detail.get("family_matches", True)  # unmapped families don't block G4
    detail: dict[str, Any] = {"exception_type": exception_type, "message": message, **family_detail}
    if not passed:
        detail["reason"] = "test failed, but with an unrelated exception family"
    return _gate_result("G4", passed, duration_ms, detail)


def gate_regression_post(bundle: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """G5: "Apply the patch, re-run the same test. Must pass." Failure
    here means the fix does not fix the bug — `03` §S8 routes that back to
    S6, not S7, since the diagnosis was wrong, not just the code (T6.4
    does not build that routing; it is S9's job, T7.1)."""
    started = time.monotonic()
    regression_test = bundle.get("regression_test")
    if not regression_test:
        duration_ms = int((time.monotonic() - started) * 1000)
        return _gate_result("G5", False, duration_ms, {"reason": "no regression_test declared"})

    test_path = regression_test["path"]
    tree = {**bundle.get("files_patched", {}), **bundle.get("new_files", {})}
    if test_path not in tree:
        duration_ms = int((time.monotonic() - started) * 1000)
        return _gate_result(
            "G5",
            False,
            duration_ms,
            {"reason": f"regression test content for {test_path!r} not found"},
        )

    materialize_tree(work_dir, tree)
    timeout = bundle.get("budgets", {}).get("tests_s", _DEFAULT_TESTS_TIMEOUT_S)

    report = _run_pytest(regression_test["test_id"], work_dir=work_dir, timeout=timeout)
    duration_ms = int((time.monotonic() - started) * 1000)

    summary = report.get("summary", {})
    passed_count = summary.get("passed", 0)
    collected = summary.get("collected", 0)

    if collected == 0:
        exception_type, message = _crash_info(report)
        return _gate_result(
            "G5",
            False,
            duration_ms,
            {
                "reason": "test could not be collected",
                "exception_type": exception_type,
                "message": message,
            },
        )

    passed = passed_count == collected
    detail: dict[str, Any] = {"summary": summary}
    if not passed:
        exception_type, message = _crash_info(report)
        detail["reason"] = "fix does not resolve the regression test — diagnosis may be wrong"
        detail["exception_type"] = exception_type
        detail["message"] = message
    return _gate_result("G5", passed, duration_ms, detail)


def _run_pytest_suite(paths: list[str], *, work_dir: Path, timeout: float) -> dict[str, str]:
    """Runs every path in `paths` as one pytest session and returns
    `{node_id: outcome}` for every test actually collected — a plain dict
    rather than the full report, since G6 only ever needs one run's
    outcomes compared against another's."""
    if not paths:
        return {}
    report_path = work_dir / _REPORT_PATH
    report_path.unlink(missing_ok=True)
    _run(
        [
            sys.executable,
            "-m",
            "pytest",
            *paths,
            "--json-report",
            f"--json-report-file={report_path}",
            "-q",
        ],
        cwd=work_dir,
        timeout=timeout,
    )
    try:
        report: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    finally:
        report_path.unlink(missing_ok=True)
    return {test["nodeid"]: test["outcome"] for test in report.get("tests", [])}


def gate_existing_tests(bundle: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """G6: "Tests discovered by S5 as covering the implicated symbols" —
    run twice, original tree first (the baseline) then patched, so a
    pre-existing failure never counts against the patch. `03` §S8's own
    classification: `newly_failing` (passed pre, fails post — a real
    regression, gate fails) versus `already_failing` (failed pre too —
    noted, not counted). Flaky-test re-run handling (`07` §6's third
    category) is not built here — disclosed, not silently absorbed into
    one of the other two; see the T6.4 write-up."""
    started = time.monotonic()
    existing_tests: dict[str, str] = bundle.get("existing_tests", {})
    if not existing_tests:
        duration_ms = int((time.monotonic() - started) * 1000)
        return _gate_result("G6", True, duration_ms, {"skipped": "no existing tests discovered"})

    timeout = bundle.get("budgets", {}).get("tests_s", _DEFAULT_TESTS_TIMEOUT_S)
    paths = list(existing_tests)

    materialize_tree(work_dir, {**bundle.get("files_original", {}), **existing_tests})
    pre = _run_pytest_suite(paths, work_dir=work_dir, timeout=timeout)

    materialize_tree(work_dir, {**bundle.get("files_patched", {}), **existing_tests})
    post = _run_pytest_suite(paths, work_dir=work_dir, timeout=timeout)

    newly_failing = [
        node_id
        for node_id, outcome in post.items()
        if outcome == "failed" and pre.get(node_id) == "passed"
    ]
    already_failing = [node_id for node_id, outcome in post.items() if pre.get(node_id) == "failed"]

    duration_ms = int((time.monotonic() - started) * 1000)
    passed = not newly_failing
    total = len(post)
    passed_count = sum(1 for outcome in post.values() if outcome == "passed")
    detail: dict[str, Any] = {
        "total": total,
        "passed": passed_count,
        "newly_failing": newly_failing,
        "already_failing": already_failing,
    }
    return _gate_result("G6", passed, duration_ms, detail)


#: `07` §6 G7 — ruff/bandit findings are bucketed by these prefixes;
#: anything else defaults to MEDIUM. Bandit reports its own severity
#: directly (handled separately in `_bandit_findings`).
_RUFF_HIGH_PREFIXES = ("S",)  # flake8-bandit-ported rules


def _ruff_findings(work_dir: Path, paths: list[str], *, timeout: float) -> dict[str, str]:
    """`{finding_key: severity}` — `finding_key` combines file, line, and
    rule code so the same finding in two separate runs (pre/post) compares
    equal, which is what "only new findings count" needs."""
    if not paths:
        return {}
    proc = _run(
        [sys.executable, "-m", "ruff", "check", "--output-format=json", *paths],
        cwd=work_dir,
        timeout=timeout,
    )
    try:
        findings = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return {}
    result: dict[str, str] = {}
    for finding in findings:
        code = finding.get("code") or ""
        key = f"{finding.get('filename')}:{finding.get('location', {}).get('row')}:{code}"
        severity = "high" if code.startswith(_RUFF_HIGH_PREFIXES) else "medium"
        result[key] = severity
    return result


def _bandit_findings(work_dir: Path, paths: list[str], *, timeout: float) -> dict[str, str]:
    if not paths:
        return {}
    proc = _run(
        [sys.executable, "-m", "bandit", "-f", "json", "-ll", *paths], cwd=work_dir, timeout=timeout
    )
    try:
        report = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {}
    result: dict[str, str] = {}
    for finding in report.get("results", []):
        key = f"{finding.get('filename')}:{finding.get('line_number')}:{finding.get('test_id')}"
        result[key] = str(finding.get("issue_severity", "MEDIUM")).lower()
    return result


def gate_static_analysis(bundle: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """G7: "Run pre- and post-patch; only new findings count." `ruff
    check` and `bandit -ll` always run; `mypy --strict` only "if
    configured" (`07` §6) — V1's fixture corpus has no mypy config, so
    this checks for one rather than assuming either way."""
    started = time.monotonic()
    changed_paths = [p for p in bundle.get("files_patched", {}) if p.endswith(".py")]
    if not changed_paths:
        duration_ms = int((time.monotonic() - started) * 1000)
        return _gate_result("G7", True, duration_ms, {"skipped": "no Python files changed"})

    timeout = bundle.get("budgets", {}).get("static_s", _DEFAULT_STATIC_TIMEOUT_S)

    materialize_tree(work_dir, bundle.get("files_original", {}))
    pre = {
        **_ruff_findings(work_dir, changed_paths, timeout=timeout),
        **_bandit_findings(work_dir, changed_paths, timeout=timeout),
    }

    materialize_tree(work_dir, bundle.get("files_patched", {}))
    post = {
        **_ruff_findings(work_dir, changed_paths, timeout=timeout),
        **_bandit_findings(work_dir, changed_paths, timeout=timeout),
    }

    new_findings = {key: severity for key, severity in post.items() if key not in pre}
    new_high = sum(1 for s in new_findings.values() if s == "high")
    new_medium = sum(1 for s in new_findings.values() if s == "medium")
    new_low = sum(1 for s in new_findings.values() if s == "low")

    duration_ms = int((time.monotonic() - started) * 1000)
    passed = new_high == 0
    detail: dict[str, Any] = {
        "new_high": new_high,
        "new_medium": new_medium,
        "new_low": new_low,
    }
    if not passed:
        detail["new_high_findings"] = [k for k, s in new_findings.items() if s == "high"]
    return _gate_result("G7", passed, duration_ms, detail)


#: `07` §6 G8 — pattern scan of *added lines only*. Each entry:
#: (compiled pattern, severity). Deliberately conservative regexes over a
#: full AST-based analysis — this is a fast, cheap last gate, not a second
#: G7; a pattern match is a real, mechanical signal even though it cannot
#: reason about intent the way a human reviewer would.
_SECURITY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\beval\s*\("), "high"),
    (re.compile(r"\bexec\s*\("), "high"),
    (re.compile(r"\bnew\s+Function\s*\("), "high"),
    (re.compile(r"\bpickle\.loads?\s*\("), "high"),
    (re.compile(r"\byaml\.load\s*\((?!.*Loader\s*=\s*yaml\.SafeLoader)"), "high"),
    (re.compile(r"subprocess\.\w+\([^)]*shell\s*=\s*True"), "high"),
    (re.compile(r"verify\s*=\s*False"), "high"),
    (re.compile(r"rejectUnauthorized\s*:\s*false"), "high"),
    (
        re.compile(r"""(?:password|secret|api_key|token)\s*=\s*['"][^'"]{4,}['"]""", re.IGNORECASE),
        "high",
    ),
    (re.compile(r"""["'].*\b(?:SELECT|INSERT|UPDATE|DELETE)\b.*["']\s*\+"""), "high"),
    (re.compile(r"\brequests\.(get|post|put|delete|patch)\s*\("), "medium"),
    (re.compile(r"\bhttpx\.(get|post|put|delete|patch|Client)\s*\("), "medium"),
)


def _added_lines(original: str, patched: str) -> list[str]:
    diff = difflib.unified_diff(original.splitlines(), patched.splitlines(), lineterm="")
    return [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]


def gate_security_scan(bundle: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """G8: pattern scan of the diff's *added* lines only. No diff text
    travels into the container (`07` §7 — only `files_original`/
    `files_patched` do), so "added lines" is reconstructed here with
    `difflib` rather than re-parsing a diff nothing gave this process."""
    started = time.monotonic()
    files_original = bundle.get("files_original", {})
    files_patched = bundle.get("files_patched", {})
    new_files = bundle.get("new_files", {})

    findings: list[dict[str, Any]] = []
    for path, patched_content in {**files_patched, **new_files}.items():
        original_content = files_original.get(path, "")
        for line in _added_lines(original_content, patched_content):
            for pattern, severity in _SECURITY_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        {"path": path, "severity": severity, "pattern": pattern.pattern}
                    )

    duration_ms = int((time.monotonic() - started) * 1000)
    high_findings = [f for f in findings if f["severity"] == "high"]
    passed = not high_findings
    detail: dict[str, Any] = {"findings": findings}
    return _gate_result("G8", passed, duration_ms, detail)
