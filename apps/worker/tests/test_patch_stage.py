"""`patch()` (`03` §S7, T5.4) — the stage entrypoint, tested against
hand-built `StructuredPatcher` doubles rather than `GatewayPatcher`, so the
stage's own defensive checks are exercised directly, same pattern
`test_reason_stage.py` establishes for S6."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from roottrace_worker.pipeline.patch.patcher import PatcherUnavailable, PatchRequest
from roottrace_worker.pipeline.patch.stage import patch
from roottrace_worker.pipeline.reason.contracts import FixStrategy, RootCause, RootCauseAnalysis
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

pytestmark = pytest.mark.unit

FILE_CONTENT = (
    "def calculate_total():\n    subtotal = base_price + tax_amount\n    return subtotal\n"
)

VALID_DIFF = (
    "--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -1,3 +1,3 @@\n"
    " def calculate_total():\n-    subtotal = base_price + tax_amount\n"
    "+    subtotal = base_price + (tax_amount or 0)\n     return subtotal\n"
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
                content=FILE_CONTENT,
                line_range=(1, 3),
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


def _analysis() -> RootCauseAnalysis:
    return RootCauseAnalysis(
        root_cause=RootCause(summary="s", mechanism="m", category="other"),
        reasoning_chain=(),
        fix_strategy=FixStrategy(
            approach="a", files_to_modify=("services/checkout.py",), regression_test_needed=False
        ),
        self_assessed_confidence=0.8,
        model="m",
        prompt_version="reason/v3",
    )


class _RaisingPatcher:
    async def patch(self, request: PatchRequest) -> Mapping[str, Any]:
        raise PatcherUnavailable("no provider available", error_code="RT-AI-0001")


class _NonMappingPatcher:
    async def patch(self, request: PatchRequest) -> Any:
        return "not a mapping"


class _UndisciplinedPatcher:
    """Skips `GatewayPatcher`'s own scope/applicability retry ladder —
    returns a scope-violating reply directly. `stage.py` must not trust a
    patcher's word for it either."""

    async def patch(self, request: PatchRequest) -> Mapping[str, Any]:
        return {
            "diff": "--- a/.github/workflows/ci.yml\n+++ b/.github/workflows/ci.yml\n"
            "@@ -1,1 +1,1 @@\n-x\n+y\n",
            "explanation": "x",
            "regression_test": None,
            "risk_assessment": {"level": "low"},
            "alternatives_considered": [],
            "model": "m",
            "prompt_version": "patch/v4",
            "tokens": {"prompt": 1, "completion": 1},
        }


class _WellBehavedPatcher:
    async def patch(self, request: PatchRequest) -> Mapping[str, Any]:
        return {
            "diff": VALID_DIFF,
            "explanation": "Guard against a None tax_amount.",
            "regression_test": None,
            "risk_assessment": {"level": "low", "breaking_change": True},
            "alternatives_considered": [],
            "model": "claude-sonnet-5",
            "prompt_version": "patch/v4",
            "tokens": {"prompt": 500, "completion": 80},
        }


class _ManifestTouchingPatcher:
    async def patch(self, request: PatchRequest) -> Mapping[str, Any]:
        return {
            "diff": "--- /dev/null\n+++ b/pyproject.toml\n@@ -0,0 +1,1 @@\n+a = 2\n",
            "explanation": "x",
            "regression_test": None,
            "risk_assessment": {"level": "low"},
            "alternatives_considered": [],
            "model": "m",
            "prompt_version": "patch/v4",
            "tokens": {"prompt": 1, "completion": 1},
        }


async def test_patcher_unavailable_propagates() -> None:
    with pytest.raises(PatcherUnavailable, match="no provider available"):
        await patch(
            _bundle(), _analysis(), patcher=_RaisingPatcher(), patch_id="pat_1", base_commit="c1"
        )


async def test_a_non_mapping_reply_raises_patcher_unavailable() -> None:
    with pytest.raises(PatcherUnavailable) as excinfo:
        await patch(
            _bundle(), _analysis(), patcher=_NonMappingPatcher(), patch_id="pat_1", base_commit="c1"
        )
    assert excinfo.value.error_code == "RT-AI-0006"


async def test_the_stage_does_not_trust_a_patchers_own_scope_discipline() -> None:
    with pytest.raises(PatcherUnavailable) as excinfo:
        await patch(
            _bundle(),
            _analysis(),
            patcher=_UndisciplinedPatcher(),
            patch_id="pat_1",
            base_commit="c1",
        )
    assert excinfo.value.error_code == "RT-AI-0005"


async def test_a_manifest_touching_patch_gets_a_scope_warning_but_still_succeeds() -> None:
    analysis = RootCauseAnalysis(
        root_cause=RootCause(summary="s", mechanism="m", category="other"),
        reasoning_chain=(),
        fix_strategy=FixStrategy(
            approach="a", files_to_modify=("pyproject.toml",), regression_test_needed=False
        ),
        self_assessed_confidence=0.8,
        model="m",
        prompt_version="reason/v3",
    )
    result = await patch(
        _bundle(), analysis, patcher=_ManifestTouchingPatcher(), patch_id="pat_1", base_commit="c1"
    )
    assert result.scope_warning is not None
    assert "pyproject.toml" in result.scope_warning


async def test_a_well_formed_reply_produces_a_patch_with_caller_supplied_ids() -> None:
    result = await patch(
        _bundle(), _analysis(), patcher=_WellBehavedPatcher(), patch_id="pat_42", base_commit="c99"
    )
    assert result.patch_id == "pat_42"
    assert result.base_commit == "c99"
    assert result.model == "claude-sonnet-5"
    assert result.tokens.prompt == 500
    assert result.risk_assessment.breaking_change is True
    assert result.files_changed[0].repo_path == "services/checkout.py"
    assert result.files_changed[0].additions == 1
    assert result.files_changed[0].deletions == 1
