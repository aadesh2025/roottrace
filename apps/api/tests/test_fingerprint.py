"""Fingerprinting (T2.3, `03` §S2, `14` §3).

> Fingerprinting deserves the most unit-test attention of anything in the
> system. Over-grouping merges distinct bugs into one issue and the AI
> investigates the wrong one; under-grouping creates a thousand issues for one
> bug and burns the cost budget. Both failures are expensive and both are
> silent.

Both directions are tested throughout: every "these group" case is paired with
a "these must not" case, because a fingerprint function that returned a
constant would pass every grouping test on its own.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from roottrace_api.ingest.fingerprint import (
    FINGERPRINT_LENGTH,
    compute_fingerprint,
    fingerprint_input,
    normalize_message,
    top_in_app_frames,
)

pytestmark = pytest.mark.unit

CORPUS = Path(__file__).resolve().parents[3] / "fixtures" / "error-corpus"


def frame(file: str, line: int, function: str, in_app: bool = True) -> dict[str, Any]:
    return {"file": file, "line": line, "function": function, "in_app": in_app}


def err(
    typ: str = "TypeError",
    msg: str = "unsupported operand type(s)",
    frames: list[dict[str, Any]] | None = None,
    route: str = "/api/v2/checkout",
) -> dict[str, Any]:
    return {
        "error": {
            "type": typ,
            "message": msg,
            "stack_frames": frames if frames is not None else [frame("checkout.py", 142, "calc")],
        },
        "request": {"route_pattern": route},
    }


# ── `14` §3's parametrised cases, verbatim ─────────────────────────────────


@pytest.mark.parametrize(
    ("a", "b", "should_match"),
    [
        # same bug, line numbers shifted by an unrelated commit
        (
            err(frames=[frame("checkout.py", 142, "calculate_total")]),
            err(frames=[frame("checkout.py", 156, "calculate_total")]),
            True,
        ),
        # same bug, variable data in the message
        (err(msg="User 8821 not found"), err(msg="User 9134 not found"), True),
        # different function, same file — genuinely different bug
        (
            err(frames=[frame("checkout.py", 142, "calculate_total")]),
            err(frames=[frame("checkout.py", 142, "apply_discount")]),
            False,
        ),
        # same function, different exception type
        (err(typ="TypeError"), err(typ="ValueError"), False),
    ],
)
def test_fingerprint_grouping(a: dict[str, Any], b: dict[str, Any], should_match: bool) -> None:
    assert (compute_fingerprint(a) == compute_fingerprint(b)) is should_match


def test_a_different_route_is_a_different_issue() -> None:
    """The same exception from two endpoints is two bugs to whoever owns them."""
    assert compute_fingerprint(err(route="/api/v2/checkout")) != compute_fingerprint(
        err(route="/api/v2/cart")
    )


def test_the_fingerprint_is_32_hex_characters() -> None:
    value = compute_fingerprint(err())
    assert len(value) == FINGERPRINT_LENGTH
    assert set(value) <= set("0123456789abcdef")


def test_the_fingerprint_is_stable_across_runs() -> None:
    """Hashing must not depend on dict ordering or anything else incidental —
    a fingerprint that drifts re-opens every issue in the project."""
    assert compute_fingerprint(err()) == compute_fingerprint(err())


# ── normalize_message: every rule ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("User 8821 not found", "User <num> not found"),
        ("id 550e8400-e29b-41d4-a716-446655440000 missing", "id <uuid> missing"),
        ("hash deadbeefcafe1234", "hash <hex>"),
        ("object at 0x7f9c2b1e", "object at <addr>"),
        ("failed for ada@example.com", "failed for <email>"),
        ("GET https://api.example.com/v2/x failed", "GET <url> failed"),
        ("from 192.168.1.55 refused", "from <ip> refused"),
        ("at 2026-08-04T09:14:22.481Z", "at <ts>"),
        ("cannot open '/app/services/checkout.py'", "cannot open <str>"),
        ("cannot open /app/services/checkout.py", "cannot open <path>"),
        ("expected 'Decimal' got 'NoneType'", "expected <str> got <str>"),
    ],
)
def test_every_normalisation_rule(raw: str, expected: str) -> None:
    assert normalize_message(raw) == expected


def test_normalisation_is_idempotent() -> None:
    """`14` §3 asks for it explicitly. A normaliser that keeps rewriting its own
    output makes the fingerprint depend on how many times it was computed."""
    once = normalize_message("User 8821 at 0x7f9c from ada@example.com")
    assert normalize_message(once) == once


def test_normalisation_is_unicode_safe() -> None:
    """`14` §3. Error messages carry whatever the customer's data contained."""
    assert normalize_message("utilisateur 8821 non trouvé — 日本語") == (
        "utilisateur <num> non trouvé — 日本語"
    )


def test_normalisation_collapses_whitespace() -> None:
    """Otherwise a message reflowed by a logging change fingerprints anew."""
    assert normalize_message("a   b\n\tc") == "a b c"


def test_an_absent_message_is_empty_not_an_error() -> None:
    assert normalize_message(None) == ""


def test_small_integers_survive() -> None:
    """`03` §S2 normalises integers of three digits or more. HTTP 404 and a
    two-digit retry count carry meaning; an id does not."""
    assert normalize_message("attempt 2 of 3") == "attempt 2 of 3"


# ── top_in_app_frames ──────────────────────────────────────────────────────


def test_vendor_frames_are_excluded() -> None:
    """`14` §3: correct exclusion of stdlib and vendor paths."""
    frames = [
        frame("/site-packages/httpx/_client.py", 10, "send", in_app=False),
        frame("/app/services/checkout.py", 142, "calculate_total"),
    ]
    assert top_in_app_frames(frames) == ["checkout.py::calculate_total"]


def test_only_the_deepest_five_frames_count() -> None:
    frames = [frame(f"m{index}.py", index, f"f{index}") for index in range(8)]
    reduced = top_in_app_frames(frames)

    assert len(reduced) == 5
    assert reduced[0] == "m0.py::f0"


def test_frames_reduce_to_basename_and_function() -> None:
    """A deploy that moves the checkout into a package must not re-fingerprint
    every one of its errors."""
    assert top_in_app_frames([frame("/app/services/checkout.py", 1, "calc")]) == [
        "checkout.py::calc"
    ]
    assert top_in_app_frames([frame("C:\\build\\app\\checkout.py", 1, "calc")]) == [
        "checkout.py::calc"
    ]


def test_no_frames_is_not_an_error() -> None:
    """A payload may carry only a stack_trace string (`03` §S1 marks
    stack_frames optional), and it still has to fingerprint."""
    assert top_in_app_frames(None) == []
    assert compute_fingerprint({"error": {"type": "TypeError", "message": "x"}})


# ── Custom rules ───────────────────────────────────────────────────────────


def test_a_custom_rule_groups_by_status_code() -> None:
    """`03` §S2's first example: HTTPError grouped by route and status rather
    than by message, so a 404 storm is one issue and a 500 is another."""
    rules = [
        {
            "match": {"error.type": "HTTPError"},
            "group_by": ["error.type", "request.route_pattern", "request.status_code"],
        }
    ]

    def http(status: int, message: str) -> dict[str, Any]:
        event = err(typ="HTTPError", msg=message)
        event["request"]["status_code"] = status
        return event

    assert compute_fingerprint(http(404, "not found: a"), rules) == compute_fingerprint(
        http(404, "not found: b"), rules
    )
    assert compute_fingerprint(http(404, "x"), rules) != compute_fingerprint(http(500, "x"), rules)


def test_a_wildcard_match_selects_the_rule() -> None:
    rules = [{"match": {"service": "worker-*"}, "group_by": ["error.type"]}]
    event = {**err(), "service": "worker-export"}

    assert fingerprint_input(event, rules) == ["TypeError"]
    assert fingerprint_input({**err(), "service": "api"}, rules) != ["TypeError"]


def test_a_rule_that_does_not_match_leaves_the_default() -> None:
    rules = [{"match": {"error.type": "HTTPError"}, "group_by": ["error.type"]}]
    assert compute_fingerprint(err(), rules) == compute_fingerprint(err())


def test_the_first_matching_rule_wins() -> None:
    """So ordering in a project's configuration is meaningful, and a later
    catch-all cannot silently swallow an earlier specific rule."""
    rules = [
        {"match": {"error.type": "TypeError"}, "group_by": ["error.type"]},
        {"match": {"error.type": "TypeError"}, "group_by": ["request.route_pattern"]},
    ]
    assert fingerprint_input(err(), rules) == ["TypeError"]


# ── Against the real corpus ────────────────────────────────────────────────


def _corpus_events() -> dict[str, dict[str, Any]]:
    events = {}
    for path in sorted(CORPUS.glob("*.json")):
        if ".case." in path.name:
            continue
        events[path.stem] = json.loads(path.read_text(encoding="utf-8"))["events"][0]
    return events


def test_the_25_corpus_cases_fingerprint_distinctly() -> None:
    """Twenty-five different bugs must be twenty-five issues.

    A collision here would merge two cases in the evaluation harness, and every
    metric computed over them would silently describe the wrong pair.
    """
    fingerprints = {case: compute_fingerprint(event) for case, event in _corpus_events().items()}

    assert len(fingerprints) == 25
    assert len(set(fingerprints.values())) == 25, fingerprints


def test_a_corpus_case_regroups_after_a_line_shift() -> None:
    """The property that matters in practice, on a real payload: move every
    frame down ten lines, as an unrelated commit above would, and the issue
    must stay the same issue."""
    event = _corpus_events()["null-prop-01"]
    shifted = json.loads(json.dumps(event))
    for item in shifted["error"]["stack_frames"]:
        item["line"] += 10

    assert compute_fingerprint(shifted) == compute_fingerprint(event)
