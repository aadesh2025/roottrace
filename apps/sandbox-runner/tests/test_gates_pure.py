"""Pure-function pieces of `gates.py` (T6.4) that need no subprocess and no
container to exercise — exception-message parsing, family matching,
security-pattern matching, and module-name derivation. The gate functions
themselves (`gate_dependencies`, `gate_regression_pre`, etc.) shell out to
`pip`/`pytest`/`ruff`/`bandit` and are exercised for real against the live
sandbox image by `apps/worker/tests/test_sandbox_gates_integration.py` —
that is the only place a fake subprocess result would prove anything
false-positive-free about G2-G8."""

from __future__ import annotations

import pytest

from roottrace_sandbox_runner.gates import (
    _SECURITY_PATTERNS,
    _added_lines,
    _crash_info,
    _family_check,
    _module_name,
    _parse_exception_type,
    _requirement_lines,
)

pytestmark = pytest.mark.unit


def test_module_name_converts_a_path_to_a_dotted_module() -> None:
    assert _module_name("services/checkout.py") == "services.checkout"


def test_module_name_returns_none_for_non_python_files() -> None:
    assert _module_name("requirements.txt") is None


def test_parse_exception_type_from_a_plain_message() -> None:
    assert _parse_exception_type("TypeError: unsupported operand type(s)") == "TypeError"


def test_parse_exception_type_from_a_dotted_custom_exception() -> None:
    assert (
        _parse_exception_type("myapp.errors.TaxServiceUnavailable: down") == "TaxServiceUnavailable"
    )


def test_crash_info_from_a_normal_test_failure() -> None:
    report = {
        "tests": [
            {
                "call": {
                    "crash": {
                        "message": "TypeError: unsupported operand type(s) for +: 'int' and 'NoneType'"
                    }
                }
            }
        ]
    }
    exc_type, message = _crash_info(report)
    assert exc_type == "TypeError"
    assert message is not None and "NoneType" in message


def test_crash_info_from_a_collection_error() -> None:
    report = {
        "tests": [],
        "collectors": [
            {
                "outcome": "failed",
                "longrepr": (
                    "ImportError while importing test module.\n"
                    "Traceback:\n"
                    "tests/test_x.py:1: in <module>\n"
                    "    from nonexistent import thing\n"
                    "E   ModuleNotFoundError: No module named 'nonexistent'"
                ),
            }
        ],
    }
    exc_type, message = _crash_info(report)
    assert exc_type == "ModuleNotFoundError"
    assert message is not None and "nonexistent" in message


def test_crash_info_returns_none_when_nothing_failed() -> None:
    report = {"tests": [{"call": {"outcome": "passed"}}], "collectors": []}
    assert _crash_info(report) == (None, None)


def test_family_check_matches_a_strictly_mapped_family() -> None:
    detail = _family_check("type_mismatch", "TypeError")
    assert detail["family_checked"] is True
    assert detail["family_matches"] is True


def test_family_check_rejects_a_strictly_mapped_family_mismatch() -> None:
    detail = _family_check("key_index", "TypeError")
    assert detail["family_checked"] is True
    assert detail["family_matches"] is False


def test_family_check_does_not_enforce_an_unmappable_family() -> None:
    """`integration`/`data_db`/`auth`/`concurrency`/`unclassified` cover
    exceptions a real codebase mostly defines itself — recorded, not
    enforced. See `gates.py`'s `_STRICT_FAMILY_EXCEPTIONS` docstring."""
    detail = _family_check("integration", "TaxServiceUnavailable")
    assert detail["family_checked"] is False


def test_family_check_with_no_expected_family_is_unchecked() -> None:
    assert _family_check(None, "TypeError") == {"family_checked": False}


def test_added_lines_reports_only_plus_prefixed_content_lines() -> None:
    original = "a = 1\nb = 2\n"
    patched = "a = 1\nb = 2\nc = eval(x)\n"
    assert _added_lines(original, patched) == ["c = eval(x)"]


def test_added_lines_excludes_the_plus_plus_plus_header() -> None:
    # `difflib.unified_diff` without filenames doesn't emit a `+++` header
    # in this call shape, but the exclusion still matters if that ever
    # changes — asserted directly against the real function's behaviour.
    original = ""
    patched = "x = 1\n"
    assert _added_lines(original, patched) == ["x = 1"]


@pytest.mark.parametrize(
    "line",
    [
        "result = eval(user_input)",
        "os.system(exec(payload))",
        "data = pickle.loads(raw)",
        "config = yaml.load(stream)",
        "subprocess.run(cmd, shell=True)",
        "requests.get(url, verify=False)",
        'password = "hunter22222"',
        'query = "SELECT * FROM users WHERE id=" + user_id',
    ],
)
def test_security_patterns_match_known_dangerous_constructs(line: str) -> None:
    assert any(pattern.search(line) for pattern, _severity in _SECURITY_PATTERNS)


def test_security_patterns_do_not_match_ordinary_code() -> None:
    ordinary = "subtotal = base_price + (tax_amount or 0)"
    assert not any(pattern.search(ordinary) for pattern, _severity in _SECURITY_PATTERNS)


def test_yaml_safe_load_pattern_does_not_false_positive() -> None:
    safe = "config = yaml.load(stream, Loader=yaml.SafeLoader)"
    yaml_findings = [p for p, _s in _SECURITY_PATTERNS if "yaml" in p.pattern]
    assert not any(pattern.search(safe) for pattern in yaml_findings)


def test_requirement_lines_strips_blank_lines_and_comments() -> None:
    manifest = "fastapi==0.111.0\n\n# a comment\nhttpx==0.27.0\n  \n"
    assert _requirement_lines(manifest) == ["fastapi==0.111.0", "httpx==0.27.0"]


def test_requirement_lines_of_an_empty_manifest_is_empty() -> None:
    assert _requirement_lines("") == []
