"""The three-attempt structured-output ladder's pure functions (`06` §4.1,
T5.1)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from roottrace_worker.ai.structured import (
    build_repair_prompt,
    parse_and_validate,
    salvage,
)

pytestmark = pytest.mark.unit


class Verdict(BaseModel):
    root_cause: str
    confidence: float


def test_valid_json_matching_the_schema_parses() -> None:
    result = parse_and_validate('{"root_cause": "null deref", "confidence": 0.8}', Verdict)
    assert result.ok
    assert result.output is not None
    assert result.output.root_cause == "null deref"


def test_invalid_json_fails_with_a_json_error() -> None:
    result = parse_and_validate("{not json", Verdict)
    assert not result.ok
    assert "invalid JSON" in (result.error or "")


def test_valid_json_missing_a_required_field_fails_validation() -> None:
    result = parse_and_validate('{"root_cause": "x"}', Verdict)
    assert not result.ok
    assert result.output is None


def test_build_repair_prompt_carries_the_original_and_the_error() -> None:
    system, user = build_repair_prompt(
        original_raw_text="{bad", validator_error="invalid JSON: line 1", schema_name="Verdict"
    )
    assert "Return ONLY the corrected JSON" in system
    assert "{bad" in user
    assert "invalid JSON: line 1" in user
    assert "Verdict" in user


def test_salvage_extracts_json_wrapped_in_prose() -> None:
    raw = 'Here is the analysis: {"root_cause": "x", "confidence": 0.5} Hope that helps!'
    salvaged = salvage(raw)
    assert salvaged is not None
    result = parse_and_validate(salvaged, Verdict)
    assert result.ok


def test_salvage_strips_markdown_fences() -> None:
    raw = '```json\n{"root_cause": "x", "confidence": 0.5}\n```'
    salvaged = salvage(raw)
    assert salvaged is not None
    result = parse_and_validate(salvaged, Verdict)
    assert result.ok


def test_salvage_removes_trailing_commas() -> None:
    raw = '{"root_cause": "x", "confidence": 0.5,}'
    salvaged = salvage(raw)
    assert salvaged is not None
    result = parse_and_validate(salvaged, Verdict)
    assert result.ok


def test_salvage_repairs_python_dict_repr_single_quotes() -> None:
    raw = "{'root_cause': 'x', 'confidence': 0.5}"
    salvaged = salvage(raw)
    assert salvaged is not None
    result = parse_and_validate(salvaged, Verdict)
    assert result.ok


def test_salvage_does_not_touch_single_quotes_inside_a_legitimate_double_quoted_string() -> None:
    """A value like `"it's broken"` must survive untouched — the single-quote
    repair is all-or-nothing specifically to avoid corrupting this."""
    raw = '{"root_cause": "it\'s broken", "confidence": 0.5}'
    salvaged = salvage(raw)
    assert salvaged is not None
    result = parse_and_validate(salvaged, Verdict)
    assert result.ok
    assert result.output is not None
    assert result.output.root_cause == "it's broken"


def test_salvage_of_pure_prose_with_no_braces_returns_the_stripped_text() -> None:
    """No brace-shaped candidate exists, so salvage cannot invent one — the
    caller's re-validation is what turns this into the honest terminal
    failure, not salvage silently succeeding at nothing."""
    salvaged = salvage("I cannot help with that request.")
    assert salvaged == "I cannot help with that request."
    result = parse_and_validate(salvaged, Verdict)
    assert not result.ok


def test_salvage_of_empty_string_returns_none() -> None:
    assert salvage("") is None
    assert salvage("   ") is None
