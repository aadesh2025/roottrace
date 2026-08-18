"""`UnderstandExtractionReply` (`03` §S4 output contract, T5.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from roottrace_worker.pipeline.understand.extraction_schema import UnderstandExtractionReply

pytestmark = pytest.mark.unit


def test_a_full_03_s4_style_reply_validates() -> None:
    reply = UnderstandExtractionReply.model_validate(
        {
            "exception": {"family": "null_undefined"},
            "frames": [{"index": 0, "confidence": 0.95}],
            "entry_point": {"type": "http_route", "method": "POST", "pattern": "/api/v2/checkout"},
            "failure_point": {"repo_path": "services/checkout.py", "function": "calculate_total"},
            "implicated_symbols": ["calculate_total"],
            "initial_hypotheses": [{"statement": "x is None", "prior": 0.5, "evidence_needed": []}],
            "retrieval_plan": {"must_fetch": ["services/checkout.py"]},
            "notes": "note",
            "extraction_confidence": 0.9,
            "suspicious_content_detected": False,
        }
    )
    assert reply.exception is not None
    assert reply.exception.family == "null_undefined"
    assert reply.frames[0].index == 0


def test_a_completely_empty_reply_validates() -> None:
    """`apply_extraction` tolerates a reply that merges nothing — the
    schema must too, since a model with genuinely nothing to add is a
    correct outcome, not a malformed one."""
    reply = UnderstandExtractionReply.model_validate({})
    assert reply.frames == []
    assert reply.initial_hypotheses == []
    assert reply.extraction_confidence == 0.5


def test_extra_fields_are_ignored_not_rejected() -> None:
    """A model that adds a field nobody asked for should not fail the
    whole call — `06` §4.1's ladder exists for real malformations, not
    harmless extras."""
    reply = UnderstandExtractionReply.model_validate({"a_field_nobody_asked_for": 1})
    assert reply is not None


def test_a_prior_outside_0_1_is_rejected() -> None:
    with pytest.raises(ValidationError):
        UnderstandExtractionReply.model_validate(
            {"initial_hypotheses": [{"statement": "x", "prior": 1.5}]}
        )


def test_extraction_confidence_outside_0_1_is_rejected() -> None:
    with pytest.raises(ValidationError):
        UnderstandExtractionReply.model_validate({"extraction_confidence": 1.5})


def test_the_schema_is_json_serialisable_for_the_gateway() -> None:
    schema = UnderstandExtractionReply.model_json_schema()
    assert schema["type"] == "object"
    assert "properties" in schema
