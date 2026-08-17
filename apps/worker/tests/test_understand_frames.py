"""Frame extraction, `in_app` classification, and path resolution steps 1 and 2.

Two properties carry the weight here. Frames must stay innermost-first, or
every failure point becomes an entry point and nothing downstream looks
obviously wrong. And an unresolved path must come back as `None` rather than
as itself, or S5 fetches `/app/services/checkout.py` from the repository root.
"""

from __future__ import annotations

import pytest

from roottrace_worker.pipeline.understand.frames import (
    CONFIDENCE_CONFIGURED,
    CONFIDENCE_HEURISTIC,
    CONFIDENCE_UNRESOLVED,
    PathMapping,
    extract_frames,
    is_in_app,
    parse_traceback,
    resolve_path,
)

pytestmark = pytest.mark.unit

APP_MAPPING = (PathMapping("/app/", ""),)


# ── Step 1: configured mappings ────────────────────────────────────────────


def test_a_configured_mapping_resolves_at_the_documented_confidence() -> None:
    resolved = resolve_path("/app/services/checkout.py", APP_MAPPING)
    assert resolved.repo_path == "services/checkout.py"
    assert resolved.confidence == CONFIDENCE_CONFIGURED


def test_a_mapping_can_target_a_subdirectory() -> None:
    """`08` §3.2's own example: `/workspace/` → `services/api/`."""
    resolved = resolve_path("/workspace/handler.py", (PathMapping("/workspace/", "services/api/"),))
    assert resolved.repo_path == "services/api/handler.py"


def test_the_longest_configured_source_wins() -> None:
    """A project that configures both `/app/` and `/app/services/` means the
    specific one; dictionary order must not decide."""
    mappings = (PathMapping("/app/", "src/"), PathMapping("/app/services/", "domain/"))
    assert resolve_path("/app/services/cart.py", mappings).repo_path == "domain/cart.py"


def test_a_configured_mapping_beats_the_heuristic() -> None:
    """Step 1 is the highest-confidence step and must be reached first, or a
    project's explicit configuration is silently ignored."""
    resolved = resolve_path("/app/x.py", (PathMapping("/app/", "packages/api/"),))
    assert resolved.repo_path == "packages/api/x.py"
    assert resolved.confidence == CONFIDENCE_CONFIGURED


# ── Step 2: heuristic prefixes ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        ("/app/services/checkout.py", "services/checkout.py"),
        ("/usr/src/app/clients/tax_client.py", "clients/tax_client.py"),
        ("/workspace/services/export.py", "services/export.py"),
        ("/srv/api/routes.py", "api/routes.py"),
        ("/var/task/handler.py", "handler.py"),
        ("/var/www/index.py", "index.py"),
        ("/home/deploy/services/cart.py", "services/cart.py"),
        ("/code/services/cart.py", "services/cart.py"),
        (r"C:\build\app\services\export.py", "services/export.py"),
        (r"C:\agent\_work\1\s\app\api\routes.py", "api/routes.py"),
    ],
)
def test_the_documented_heuristic_prefixes(reported: str, expected: str) -> None:
    """`08` §3.2 step 2, including the two entries that are patterns rather
    than literals: `/home/*/` and `C:\\...\\`."""
    resolved = resolve_path(reported)
    assert resolved.repo_path == expected
    assert resolved.confidence == CONFIDENCE_HEURISTIC


def test_the_longest_heuristic_prefix_wins() -> None:
    """Stripping `/usr/src/` from `/usr/src/app/x.py` would leave `app/x.py`,
    which is a plausible-looking path that does not exist."""
    assert resolve_path("/usr/src/app/x.py").repo_path == "x.py"


def test_an_already_relative_path_needs_no_mapping() -> None:
    resolved = resolve_path("services/checkout.py")
    assert resolved.repo_path == "services/checkout.py"


def test_an_unmappable_absolute_path_resolves_to_nothing() -> None:
    """Returning the input unchanged would hand S5 an absolute path to fetch
    from the repository root. `03` §S4 pins the confidence at 0.3 and hands the
    problem to S5's tree search."""
    resolved = resolve_path("/opt/mystery/thing.py")
    assert resolved.repo_path is None
    assert resolved.confidence == CONFIDENCE_UNRESOLVED


def test_the_corpus_case_that_needs_step_three_is_not_faked_here() -> None:
    """`config-02` reports `/workspace/services/services/export.py`. Stripping
    the documented `/workspace/` prefix gives a well-formed path that is not a
    file, and nothing available to S4 can tell — it has no repo access (`03`
    §8.1). S5's suffix match corrects it (T4.2). This asserts we did **not**
    quietly add a fixture-shaped mapping to make the number look better."""
    resolved = resolve_path("/workspace/services/services/export.py")
    assert resolved.repo_path == "services/services/export.py"
    assert resolved.confidence == CONFIDENCE_HEURISTIC


# ── in_app classification ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/app/.venv/lib/python3.12/site-packages/httpx/_client.py",
        "/usr/lib/python3.12/json/decoder.py",
        "/app/node_modules/express/lib/router.js",
        "/app/vendor/bundle/gem.rb",
        "/usr/local/lib/python3.12/dist-packages/urllib3/connection.py",
        "<frozen importlib._bootstrap>",
        "<string>",
    ],
)
def test_dependency_and_runtime_frames_are_not_in_app(path: str) -> None:
    assert is_in_app(path) is False


@pytest.mark.parametrize(
    "path", ["/app/services/checkout.py", "services/cart.py", r"C:\build\app\services\export.py"]
)
def test_application_frames_are_in_app(path: str) -> None:
    assert is_in_app(path) is True


def test_a_payload_may_demote_a_frame() -> None:
    """A client knows its own project root and may legitimately mark a frame
    out-of-app that looks in-app to us."""
    error = {"stack_frames": [{"file": "/app/services/checkout.py", "line": 1, "in_app": False}]}
    assert extract_frames(error, mappings=APP_MAPPING)[0].in_app is False


def test_a_payload_may_not_promote_a_frame() -> None:
    """The reverse is hostile input: a payload that marked the standard library
    in-app would put it inside a patch's blast radius."""
    error = {
        "stack_frames": [
            {"file": "/usr/lib/python3.12/json/decoder.py", "line": 355, "in_app": True}
        ]
    }
    frame = extract_frames(error, mappings=APP_MAPPING)[0]
    assert frame.in_app is False
    assert frame.repo_path is None


# ── Traceback text ─────────────────────────────────────────────────────────


TRACEBACK = (
    "Traceback (most recent call last):\n"
    '  File "/app/api/routes/checkout.py", line 58, in _build_response\n'
    "    total = checkout_service.calculate_total(cart, user)\n"
    '  File "/app/services/checkout.py", line 142, in calculate_total\n'
    "    subtotal = base_price + tax_amount\n"
    "TypeError: unsupported operand type(s) for +: 'decimal.Decimal' and 'NoneType'\n"
)


def test_a_traceback_is_parsed_innermost_first() -> None:
    """CPython prints outermost-first; `03` §S1 stores the opposite. Getting
    this backwards swaps the failure point and the entry point, and neither
    would look wrong on inspection."""
    frames = parse_traceback(TRACEBACK)
    assert [frame["function"] for frame in frames] == ["calculate_total", "_build_response"]
    assert frames[0]["line"] == 142


def test_frames_are_extracted_from_the_text_when_the_payload_has_none() -> None:
    """A curl'd payload or a third-party forwarder may send only the text."""
    frames = extract_frames({"stack_trace": TRACEBACK}, mappings=APP_MAPPING)
    assert [frame.repo_path for frame in frames] == [
        "services/checkout.py",
        "api/routes/checkout.py",
    ]
    assert frames[0].function == "calculate_total"


def test_structured_frames_are_preferred_over_the_text() -> None:
    """Both are usually present. The structured list carries locals and
    context lines that the text does not."""
    error = {
        "stack_trace": TRACEBACK,
        "stack_frames": [{"file": "/app/only.py", "line": 3, "function": "f"}],
    }
    frames = extract_frames(error, mappings=APP_MAPPING)
    assert len(frames) == 1
    assert frames[0].repo_path == "only.py"


def test_an_error_with_neither_yields_no_frames() -> None:
    assert extract_frames({}) == ()


def test_a_nonsense_line_number_is_dropped_rather_than_carried() -> None:
    error = {"stack_frames": [{"file": "/app/x.py", "line": 0, "function": "f"}]}
    assert extract_frames(error, mappings=APP_MAPPING)[0].line is None
