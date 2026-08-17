"""Post-validation of the extractor's reply (`03` §S4 step 3).

The merge rule is the security boundary of this stage: the extractor's reply is
model output over untrusted input, and `06` §5's guardrails start here. Every
test below is a claim about what a *hostile or hallucinating* extractor cannot
do — the plan can only improve, never degrade below the deterministic floor.
"""

from __future__ import annotations

from typing import Any

import pytest

from roottrace_worker.pipeline.understand.contracts import (
    ErrorUnderstanding,
    ExceptionFamily,
    ExceptionInfo,
    Flag,
    Frame,
    RetrievalPlan,
)
from roottrace_worker.pipeline.understand.validate import (
    apply_extraction,
    is_plausible_repo_path,
    references_only_real_frames,
)

pytestmark = pytest.mark.unit


def base_understanding() -> ErrorUnderstanding:
    return ErrorUnderstanding(
        language="python",
        framework="fastapi",
        exception=ExceptionInfo(
            type="TypeError",
            family=ExceptionFamily.NULL_UNDEFINED,
            message_normalized="unsupported operand type(s) for +: '<type>' and '<type>'",
            is_user_facing=True,
        ),
        frames=(
            Frame(
                index=0,
                raw_path="/app/services/checkout.py",
                repo_path="services/checkout.py",
                line=142,
                function="calculate_total",
                in_app=True,
                confidence=0.95,
            ),
        ),
        implicated_symbols=("calculate_total",),
        retrieval_plan=RetrievalPlan(must_fetch=("services/checkout.py",)),
        notes="deterministic",
        extraction_confidence=0.5,
        flags=(Flag.DETERMINISTIC_ONLY,),
    )


# ── Path shape ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    ["services/checkout.py", "a.py", "src/api/v2/routes.ts", "pkg/sub-module/file_name.go"],
)
def test_plausible_paths(path: str) -> None:
    assert is_plausible_repo_path(path) is True


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "/app/services/checkout.py",
        "../../secrets.env",
        "services/../../../etc/shadow",
        r"C:\Windows\System32\config",
        "services/checkout",
        ".venv/lib/python3.12/site-packages/httpx/_client.py",
        "node_modules/express/index.js",
        "",
        None,
        123,
    ],
)
def test_implausible_paths(path: Any) -> None:
    """Shape only — S4 has no repo access (`03` §8.1). This is what stops an
    absolute path or a traversal reaching S5's fetch loop at all."""
    assert is_plausible_repo_path(path) is False


# ── Frame references ───────────────────────────────────────────────────────


def test_a_claim_may_only_cite_frames_that_exist() -> None:
    assert references_only_real_frames("see frame 0", 1) is True
    assert references_only_real_frames("see frame 7", 1) is False
    assert references_only_real_frames("frames[2] shows the caller", 2) is False


def test_a_claim_citing_no_frame_is_fine() -> None:
    assert references_only_real_frames("the tax client swallows the error", 1) is True


# ── The merge ──────────────────────────────────────────────────────────────


def test_the_model_may_add_a_file_to_the_plan() -> None:
    merged, dropped = apply_extraction(
        base_understanding(), {"retrieval_plan": {"must_fetch": ["clients/tax_client.py"]}}
    )
    assert merged.retrieval_plan.must_fetch == ("services/checkout.py", "clients/tax_client.py")
    assert dropped == ()


def test_the_model_may_not_remove_a_file_the_frames_prove_was_executing() -> None:
    """A reply naming only one file is an *addition*, never a replacement."""
    merged, _ = apply_extraction(
        base_understanding(), {"retrieval_plan": {"must_fetch": ["clients/tax_client.py"]}}
    )
    assert "services/checkout.py" in merged.retrieval_plan.must_fetch


def test_an_implausible_path_is_dropped_and_recorded() -> None:
    merged, dropped = apply_extraction(
        base_understanding(), {"retrieval_plan": {"must_fetch": ["/etc/passwd"]}}
    )
    assert "/etc/passwd" not in merged.retrieval_plan.must_fetch
    assert any("/etc/passwd" in claim for claim in dropped)


def test_the_model_may_lower_a_frame_confidence() -> None:
    """`A2` §3 step 2 asks it to mark mappings that look wrong."""
    merged, _ = apply_extraction(
        base_understanding(), {"frames": [{"index": 0, "confidence": 0.4}]}
    )
    assert merged.frames[0].confidence == 0.4


def test_the_model_may_not_raise_a_frame_confidence() -> None:
    """Confidence is earned by the cascade step that resolved the path
    (`08` §3.2). A model asserting 0.99 over a guess would present a guess as
    configuration."""
    merged, dropped = apply_extraction(
        base_understanding(), {"frames": [{"index": 0, "confidence": 0.99}]}
    )
    assert merged.frames[0].confidence == 0.95
    assert any("refused to raise" in claim for claim in dropped)


def test_the_model_may_not_invent_a_frame() -> None:
    merged, dropped = apply_extraction(
        base_understanding(), {"frames": [{"index": 9, "confidence": 0.9}]}
    )
    assert len(merged.frames) == 1
    assert any("no such frame" in claim for claim in dropped)


def test_the_model_may_replace_the_family() -> None:
    """The one classification it is better at: it reads breadcrumbs, and the
    deterministic taxonomy deliberately does not."""
    merged, _ = apply_extraction(base_understanding(), {"exception": {"family": "concurrency"}})
    assert merged.exception.family is ExceptionFamily.CONCURRENCY


def test_an_unknown_family_is_dropped() -> None:
    merged, dropped = apply_extraction(
        base_understanding(), {"exception": {"family": "cosmic_rays"}}
    )
    assert merged.exception.family is ExceptionFamily.NULL_UNDEFINED
    assert any("cosmic_rays" in claim for claim in dropped)


def test_the_runtime_metadata_cannot_be_contradicted() -> None:
    """`language` and `framework` are facts the SDK reported. A model
    overriding them is simply wrong, so the merge reads a fixed set of keys and
    these are not among them."""
    merged, _ = apply_extraction(base_understanding(), {"language": "ruby", "framework": "rails"})
    assert (merged.language, merged.framework) == ("python", "fastapi")


def test_hypotheses_are_accepted_with_their_priors() -> None:
    merged, _ = apply_extraction(
        base_understanding(),
        {
            "initial_hypotheses": [
                {
                    "statement": "the tax service returned 503",
                    "prior": 0.65,
                    "evidence_needed": ["the tax client"],
                },
                {"statement": "no tax configuration for this region", "prior": 0.25},
            ]
        },
    )
    assert len(merged.initial_hypotheses) == 2
    assert merged.initial_hypotheses[0].prior == 0.65


def test_priors_are_truncated_where_they_exceed_one() -> None:
    """`A2` §3: priors sum to at most 1.0. Truncating the tail keeps the
    surviving priors meaning what the model said; renormalising would silently
    promote a 0.05 afterthought."""
    merged, dropped = apply_extraction(
        base_understanding(),
        {
            "initial_hypotheses": [
                {"statement": "a", "prior": 0.7},
                {"statement": "b", "prior": 0.6},
            ]
        },
    )
    assert [h.statement for h in merged.initial_hypotheses] == ["a"]
    assert any("exceed 1.0" in claim for claim in dropped)


def test_a_hypothesis_citing_a_nonexistent_frame_is_dropped() -> None:
    merged, dropped = apply_extraction(
        base_understanding(),
        {"initial_hypotheses": [{"statement": "frame 4 shows the caller", "prior": 0.5}]},
    )
    assert merged.initial_hypotheses == ()
    assert any("nonexistent frame" in claim for claim in dropped)


def test_a_malformed_hypothesis_does_not_take_the_others_down() -> None:
    merged, _ = apply_extraction(
        base_understanding(),
        {"initial_hypotheses": ["not an object", {"statement": "real", "prior": 0.4}]},
    )
    assert [h.statement for h in merged.initial_hypotheses] == ["real"]


@pytest.mark.parametrize(
    ("hypothesis", "reason"),
    [
        ({"statement": "", "prior": 0.5}, "initial_hypotheses:"),
        ({"statement": None, "prior": 0.5}, "initial_hypotheses:"),
        ({"prior": 0.5}, "initial_hypotheses:"),
        ({"statement": "a", "prior": "very likely"}, "unreadable prior"),
        ({"statement": "a", "prior": 1.4}, "prior out of range"),
        ({"statement": "a", "prior": -0.2}, "prior out of range"),
    ],
)
def test_every_way_a_hypothesis_can_be_unusable(hypothesis: Any, reason: str) -> None:
    """Each of these is a shape a model has produced somewhere. None of them
    may reach S6, and none of them may take the stage down."""
    merged, dropped = apply_extraction(base_understanding(), {"initial_hypotheses": [hypothesis]})
    assert merged.initial_hypotheses == ()
    assert any(reason in claim for claim in dropped)


def test_at_most_four_hypotheses_are_kept() -> None:
    """`A2` §3 asks for 2 to 4. A model that returns twenty is not being more
    helpful, and each one costs S5 budget."""
    merged, _ = apply_extraction(
        base_understanding(),
        {"initial_hypotheses": [{"statement": f"h{n}", "prior": 0.1} for n in range(9)]},
    )
    assert len(merged.initial_hypotheses) == 4


def test_evidence_needed_citing_a_nonexistent_frame_is_dropped_without_the_hypothesis() -> None:
    """The hypothesis may be sound while one of its evidence lines is not.
    Dropping the claim means dropping the unsupportable part, not the whole."""
    merged, _ = apply_extraction(
        base_understanding(),
        {
            "initial_hypotheses": [
                {
                    "statement": "the tax client swallows the error",
                    "prior": 0.6,
                    "evidence_needed": ["frame 0's caller", "frame 8's caller"],
                }
            ]
        },
    )
    assert merged.initial_hypotheses[0].evidence_needed == ("frame 0's caller",)


@pytest.mark.parametrize(
    "frame_reply",
    [
        "not a list",
        ["not an object"],
        [{"index": "zero", "confidence": 0.4}],
        [{"index": 0, "confidence": "high"}],
        [{"index": 0}],
    ],
)
def test_a_malformed_frame_assessment_is_ignored(frame_reply: Any) -> None:
    merged, _ = apply_extraction(base_understanding(), {"frames": frame_reply})
    assert merged.frames == base_understanding().frames


def test_a_frame_confidence_outside_zero_to_one_is_dropped() -> None:
    merged, dropped = apply_extraction(
        base_understanding(), {"frames": [{"index": 0, "confidence": 7.0}]}
    )
    assert merged.frames[0].confidence == 0.95
    assert any("out of range" in claim for claim in dropped)


def test_a_negative_frame_index_is_not_python_indexing() -> None:
    """`frames[-1]` would silently reassess the *last* frame. A model that
    said -1 meant something we cannot honour."""
    merged, dropped = apply_extraction(
        base_understanding(), {"frames": [{"index": -1, "confidence": 0.1}]}
    )
    assert merged.frames == base_understanding().frames
    assert any("no such frame" in claim for claim in dropped)


def test_a_non_string_claim_references_nothing_real() -> None:
    assert references_only_real_frames(None, 3) is False
    assert references_only_real_frames(42, 3) is False


def test_the_deterministic_flag_is_cleared_on_a_successful_extraction() -> None:
    merged, _ = apply_extraction(base_understanding(), {"extraction_confidence": 0.91})
    assert Flag.DETERMINISTIC_ONLY not in merged.flags
    assert merged.extraction_confidence == 0.91


def test_an_out_of_range_confidence_is_clamped() -> None:
    merged, _ = apply_extraction(base_understanding(), {"extraction_confidence": 4.2})
    assert merged.extraction_confidence == 1.0


def test_an_empty_reply_changes_nothing_but_the_flag() -> None:
    base = base_understanding()
    merged, dropped = apply_extraction(base, {})
    assert dropped == ()
    assert merged.retrieval_plan == base.retrieval_plan
    assert merged.extraction_confidence == base.extraction_confidence


def test_a_hostile_reply_degrades_to_the_deterministic_plan() -> None:
    """The property the whole merge exists for, as one test: an extractor that
    returns nothing usable leaves the plan exactly as the pre-parse built it."""
    base = base_understanding()
    merged, dropped = apply_extraction(
        base,
        {
            "retrieval_plan": {
                "must_fetch": ["/etc/passwd", "../../../root/.ssh/id_rsa"],
                "should_fetch_by_symbol": ["rm -rf /", "'; drop table issues; --"],
                "want_tests_for": ["../evil"],
            },
            "frames": [{"index": 0, "confidence": 1.0}, {"index": 99, "confidence": 0.1}],
            "initial_hypotheses": [{"statement": "frame 12 proves it", "prior": 0.9}],
            "exception": {"family": "not_a_family"},
        },
    )
    assert merged.retrieval_plan == base.retrieval_plan
    assert merged.frames == base.frames
    assert merged.initial_hypotheses == ()
    assert merged.exception.family == base.exception.family
    assert len(dropped) >= 6
