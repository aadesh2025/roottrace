"""`reason()` (`03` §S6, T5.3) — the stage entrypoint, tested against
hand-built `StructuredReasoner` doubles rather than `GatewayReasoner`, so
the stage's own defensive checks (a reasoner returning something that
isn't a mapping, or skipping its own evidence-binding discipline) are
exercised directly rather than only reachable through a real gateway call.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from roottrace_worker.github.types import Actor, Commit
from roottrace_worker.pipeline.reason.reasoner import ReasonerUnavailable, ReasonRequest
from roottrace_worker.pipeline.reason.stage import ReasonOutcome, reason
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
from roottrace_worker.pipeline.understand.contracts import (
    ErrorUnderstanding,
    ExceptionFamily,
    ExceptionInfo,
    RetrievalPlan,
)

pytestmark = pytest.mark.unit

COMMIT = Commit(
    sha="8a3f1c2e" + "0" * 32,
    message="m",
    author=Actor(name="d", email="d@x.io"),
    date=datetime(2026, 7, 25, tzinfo=UTC),
)


def _bundle() -> ContextBundle:
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
                content="def f():\n    return 1\n",
                line_range=(1, 2),
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


def _understanding() -> ErrorUnderstanding:
    return ErrorUnderstanding(
        exception=ExceptionInfo(
            type="TypeError",
            family=ExceptionFamily.NULL_UNDEFINED,
            message_normalized="m",
            is_user_facing=True,
        ),
        retrieval_plan=RetrievalPlan(),
        extraction_confidence=0.9,
    )


class _RaisingReasoner:
    async def reason(self, request: ReasonRequest) -> Mapping[str, Any]:
        raise ReasonerUnavailable("no provider available")


class _NonMappingReasoner:
    async def reason(self, request: ReasonRequest) -> Any:
        return "not a mapping"


class _UndisciplinedReasoner:
    """Skips `GatewayReasoner`'s own evidence-binding retry — returns an
    unbound reply directly. `stage.py` must not trust a reasoner's word
    for it either."""

    async def reason(self, request: ReasonRequest) -> Mapping[str, Any]:
        return {
            "root_cause": {"summary": "s", "mechanism": "m", "category": "other"},
            "reasoning_chain": [
                {
                    "step": 1,
                    "type": "conclude",
                    "statement": "y",
                    "evidence": [
                        {
                            "kind": "file",
                            "repo_path": "services/checkout.py",
                            "line_range": [1, 2],
                            "excerpt": "fabricated, never in the file",
                        }
                    ],
                }
            ],
            "fix_strategy": {"approach": "a", "files_to_modify": ["services/checkout.py"]},
            "self_assessed_confidence": 0.5,
        }


async def test_reasoner_unavailable_terminates_as_insufficient_context() -> None:
    outcome = await reason(_bundle(), _understanding(), reasoner=_RaisingReasoner())
    assert not outcome.ok
    assert outcome.insufficient is not None
    assert "no provider available" in outcome.insufficient.explanation


async def test_a_non_mapping_reply_terminates_as_insufficient_context() -> None:
    outcome = await reason(_bundle(), _understanding(), reasoner=_NonMappingReasoner())
    assert not outcome.ok
    assert outcome.insufficient is not None
    assert "non-mapping" in outcome.insufficient.explanation


async def test_the_stage_does_not_trust_a_reasoners_own_evidence_binding_word() -> None:
    outcome = await reason(_bundle(), _understanding(), reasoner=_UndisciplinedReasoner())
    assert not outcome.ok
    assert outcome.insufficient is not None
    assert "evidence binding" in outcome.insufficient.explanation


def test_reason_outcome_requires_exactly_one_of_analysis_or_insufficient() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ReasonOutcome()


def test_reason_outcome_rejects_both_analysis_and_insufficient_set() -> None:
    from roottrace_worker.pipeline.reason.contracts import (
        FixStrategy,
        ReasoningStep,
        RootCause,
        RootCauseAnalysis,
    )

    analysis = RootCauseAnalysis(
        root_cause=RootCause(summary="s", mechanism="m", category="other"),
        reasoning_chain=(ReasoningStep(step=1, type="conclude", statement="s"),),
        fix_strategy=FixStrategy(approach="a"),
        self_assessed_confidence=0.5,
        model="m",
        prompt_version="v1",
    )
    insufficient = _bundle()  # any object works for this arity check
    with pytest.raises(ValueError, match="exactly one"):
        ReasonOutcome(analysis=analysis, insufficient=insufficient)  # type: ignore[arg-type]
