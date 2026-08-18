"""`GatewayPatcher` (T5.4) — the real `StructuredPatcher`, exercised
end-to-end through `patch(...)` with a `FakeProvider` standing in for the
model. Mirrors `test_reason_gateway_reasoner.py`'s shape."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest

from roottrace_worker.ai.contracts import LLMCallRecord
from roottrace_worker.ai.gateway import LLMGateway
from roottrace_worker.ai.prompts.registry import load_prompt_registry
from roottrace_worker.ai.providers.fake import FakeProvider, ScriptedFailure, ScriptedSuccess
from roottrace_worker.ai.routing import parse_model_routing
from roottrace_worker.ai.storage import InMemoryObjectStore
from roottrace_worker.github.types import Actor, Commit
from roottrace_worker.pipeline.patch import GatewayPatcher, patch
from roottrace_worker.pipeline.patch.patcher import PatcherUnavailable
from roottrace_worker.pipeline.reason.contracts import FixStrategy, RootCause, RootCauseAnalysis
from roottrace_worker.pipeline.retrieve.bundle import (
    BlameInfo,
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

_PROMPTS = load_prompt_registry()

FILE_CONTENT = (
    "def calculate_total():\n    subtotal = base_price + tax_amount\n    return subtotal\n"
)

VALID_DIFF = (
    "--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -1,3 +1,3 @@\n"
    " def calculate_total():\n-    subtotal = base_price + tax_amount\n"
    "+    subtotal = base_price + (tax_amount or 0)\n     return subtotal\n"
)

VALID_REPLY = json.dumps(
    {
        "diff": VALID_DIFF,
        "explanation": "Guard against a None tax_amount.",
        "regression_test": None,
        "risk_assessment": {"level": "low"},
        "alternatives_considered": [
            {"approach": "default to 0", "rejected_because": "under-charges customers"}
        ],
    }
)

SCOPE_VIOLATION_DIFF = (
    "--- a/.github/workflows/ci.yml\n+++ b/.github/workflows/ci.yml\n@@ -1,1 +1,1 @@\n-x\n+y\n"
)
SCOPE_VIOLATION_REPLY = json.dumps(
    {
        "diff": SCOPE_VIOLATION_DIFF,
        "explanation": "oops",
        "regression_test": None,
        "risk_assessment": {"level": "low"},
        "alternatives_considered": [],
    }
)


def _bundle() -> ContextBundle:
    return ContextBundle(
        bundle_id="ctx_1",
        repository=RepositoryRef(full_name="acme/checkout-api", ref="main", commit_sha="deadbeef"),
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
            approach="guard",
            files_to_modify=("services/checkout.py",),
            considerations=("Preserve the existing warning log.",),
            regression_test_needed=False,
        ),
        self_assessed_confidence=0.8,
        model="claude-sonnet-5",
        prompt_version="reason/v3",
    )


def _routing_doc() -> dict[str, Any]:
    return {
        "tiers": {
            "fast": [{"provider": "anthropic", "model": "claude-haiku-4-5"}],
            "reasoning-a": [{"provider": "anthropic", "model": "claude-sonnet-5"}],
            "reasoning-b": [{"provider": "anthropic", "model": "claude-sonnet-5"}],
            "embed": [{"provider": "voyage", "model": "voyage-code-3", "dimensions": 1536}],
        },
        "failover": {
            "trigger_on": ["rate_limit", "timeout", "server_error", "content_filter"],
            "max_provider_attempts": 1,
            "backoff": {"base_ms": 1, "factor": 1, "jitter": False, "max_ms": 1},
        },
    }


@dataclass(slots=True)
class InMemoryLLMCallsRepository:
    records: list[LLMCallRecord] = field(default_factory=list)

    async def insert(self, record: LLMCallRecord) -> str:
        self.records.append(record)
        return f"call_{len(self.records)}"


async def _noop_sleep(_seconds: float) -> None:
    return None


def _patcher(
    provider: FakeProvider, *, db: InMemoryLLMCallsRepository | None = None
) -> GatewayPatcher:
    gateway = LLMGateway(
        providers={"anthropic": provider},
        routing=parse_model_routing(_routing_doc()),
        prompts=_PROMPTS,
        storage=InMemoryObjectStore(),
        db=db or InMemoryLLMCallsRepository(),
        sleep=_noop_sleep,
    )
    return GatewayPatcher(gateway=gateway, prompts=_PROMPTS, project_id="proj_1")


async def test_a_clean_diff_produces_a_patch() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    result = await patch(
        _bundle(), _analysis(), patcher=_patcher(provider), patch_id="pat_1", base_commit="deadbeef"
    )

    assert result.patch_id == "pat_1"
    assert result.base_commit == "deadbeef"
    assert result.diff == VALID_DIFF
    assert len(result.files_changed) == 1
    assert result.files_changed[0].repo_path == "services/checkout.py"
    assert result.risk_assessment.level == "low"
    assert len(result.alternatives_considered) == 1


async def test_the_call_reaches_the_provider_with_the_assembled_prompt() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    await patch(
        _bundle(), _analysis(), patcher=_patcher(provider), patch_id="pat_1", base_commit="deadbeef"
    )

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert "<untrusted_context>" in call.user
    assert "def calculate_total" in call.user
    assert "files_to_modify" in call.user  # the fix_strategy block, fenced as data
    assert call.model == "claude-sonnet-5"  # reasoning-a tier


async def test_model_and_tokens_come_from_the_real_llm_result_not_the_reply() -> None:
    provider = FakeProvider(
        name="anthropic",
        outcomes=[ScriptedSuccess(VALID_REPLY, tokens_in=21_000, tokens_out=1_800)],
    )
    result = await patch(
        _bundle(), _analysis(), patcher=_patcher(provider), patch_id="pat_1", base_commit="deadbeef"
    )

    assert result.model == "claude-sonnet-5"
    assert result.tokens.prompt == 21_000
    assert result.tokens.completion == 1_800


async def test_a_scope_violation_triggers_one_correction_retry_then_fails() -> None:
    provider = FakeProvider(
        name="anthropic",
        outcomes=[ScriptedSuccess(SCOPE_VIOLATION_REPLY), ScriptedSuccess(SCOPE_VIOLATION_REPLY)],
    )
    with pytest.raises(PatcherUnavailable) as excinfo:
        await patch(
            _bundle(),
            _analysis(),
            patcher=_patcher(provider),
            patch_id="pat_1",
            base_commit="deadbeef",
        )

    assert len(provider.calls) == 2
    assert excinfo.value.error_code == "RT-AI-0005"
    # The correction instruction lives in L3 (task), which `assemble_prompt`
    # places in `system`, not `user` — same as T5.3's identical assertion.
    assert "CORRECTION NEEDED" in provider.calls[1].system


async def test_a_scope_violation_that_clears_on_retry_succeeds() -> None:
    provider = FakeProvider(
        name="anthropic",
        outcomes=[ScriptedSuccess(SCOPE_VIOLATION_REPLY), ScriptedSuccess(VALID_REPLY)],
    )
    result = await patch(
        _bundle(), _analysis(), patcher=_patcher(provider), patch_id="pat_1", base_commit="deadbeef"
    )
    assert len(provider.calls) == 2
    assert result.diff == VALID_DIFF


async def test_a_non_applying_diff_fails_with_the_diff_specific_code() -> None:
    bad_diff = (
        "--- a/services/checkout.py\n+++ b/services/checkout.py\n@@ -1,3 +1,3 @@\n"
        " def calculate_total():\n-    never in the file\n"
        "+    subtotal = base_price + (tax_amount or 0)\n     return subtotal\n"
    )
    bad_reply = json.dumps(
        {
            "diff": bad_diff,
            "explanation": "x",
            "regression_test": None,
            "risk_assessment": {"level": "low"},
            "alternatives_considered": [],
        }
    )
    provider = FakeProvider(
        name="anthropic", outcomes=[ScriptedSuccess(bad_reply), ScriptedSuccess(bad_reply)]
    )
    with pytest.raises(PatcherUnavailable) as excinfo:
        await patch(
            _bundle(),
            _analysis(),
            patcher=_patcher(provider),
            patch_id="pat_1",
            base_commit="deadbeef",
        )
    assert excinfo.value.error_code == "RT-AI-0006"


async def test_a_file_with_blame_includes_the_introducing_commit_sha_in_the_prompt() -> None:
    commit = Commit(
        sha="8a3f1c2e" + "0" * 32,
        message="refactor: extract tax lookup",
        author=Actor(name="d", email="d@x.io"),
        date=datetime(2026, 7, 25, tzinfo=UTC),
    )
    bundle = ContextBundle(
        bundle_id="ctx_1",
        repository=RepositoryRef(full_name="acme/checkout-api", ref="main", commit_sha="deadbeef"),
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
                blame=BlameInfo(line=2, commit=commit),
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
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    await patch(
        bundle, _analysis(), patcher=_patcher(provider), patch_id="pat_1", base_commit="deadbeef"
    )

    assert commit.sha in provider.calls[0].user


async def test_gateway_exhaustion_raises_patcher_unavailable_with_the_generic_code() -> None:
    provider = FakeProvider(
        name="anthropic", outcomes=[ScriptedFailure(trigger="server_error", detail="down")]
    )
    with pytest.raises(PatcherUnavailable) as excinfo:
        await patch(
            _bundle(),
            _analysis(),
            patcher=_patcher(provider),
            patch_id="pat_1",
            base_commit="deadbeef",
        )
    assert excinfo.value.error_code == "RT-AI-0001"
