"""Live smoke test for S7 `patch` against real fixture cases with a real
model — skipped unless a real API key is present, same pattern
`test_reason_live.py` establishes for S6 and `test_ai_provider_live.py`
established first.

**Not an accuracy measurement**, same reasoning as `test_reason_live.py`'s
own docstring. `15` T5.4's own `≥24/25 diffs apply cleanly` bar is a
corpus-wide statistical claim over real model output — measuring it
honestly needs the full 25-case corpus, multiple runs, and a real
evaluation harness, which is `T10.1`'s job, not this ticket's. This file
runs the full S4→S5→S6→S7 chain against a handful of real fixture cases
with real model calls end to end, and checks the mechanism does not fall
over — a well-formed `Patch` with a diff that actually parses, or an
honest `PatcherUnavailable` with a real reason — enough to catch something
catastrophically wrong before Phase 10 (sandbox) is built on top of this
stage, not enough to claim the accuracy bar is met."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from roottrace_worker.ai.gateway import LLMGateway
from roottrace_worker.ai.prompts.registry import load_prompt_registry
from roottrace_worker.ai.providers.anthropic import AnthropicProvider
from roottrace_worker.ai.routing import load_model_routing
from roottrace_worker.ai.storage import InMemoryObjectStore
from roottrace_worker.github.fixture import FixtureTransport
from roottrace_worker.github.types import RepoRef, RepoTree
from roottrace_worker.pipeline.patch import GatewayPatcher, PatcherUnavailable, patch
from roottrace_worker.pipeline.reason import GatewayReasoner, reason
from roottrace_worker.pipeline.retrieve.bundle import ContextBundle
from roottrace_worker.pipeline.retrieve.ranking import build_context_bundle
from roottrace_worker.pipeline.retrieve.strategies import gather
from roottrace_worker.pipeline.understand import PathMapping, understand

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("RT_ANTHROPIC_API_KEY"), reason="RT_ANTHROPIC_API_KEY not set"
    ),
]

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_REPO = REPO_ROOT / "fixtures" / "synthetic-repo"
CORPUS = REPO_ROOT / "fixtures" / "error-corpus"
FIXTURE_REPO_REF = RepoRef(owner="acme", name="checkout-api")

#: Five non-control cases known (from `test_reason_live.py`'s own broader
#: set) to reach S5 with a real `ContextBundle` — enough diversity to catch
#: a mechanism failure without duplicating T10.1's full-corpus measurement.
LIVE_CASE_IDS: tuple[str, ...] = (
    "null-prop-01",
    "type-mismatch-01",
    "key-error-01",
    "external-01",
    "regression-01",
)


def _payload(case_id: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((CORPUS / f"{case_id}.json").read_text(encoding="utf-8"))
    return data


def _project_mappings() -> tuple[PathMapping, ...]:
    metadata = json.loads((FIXTURE_REPO / ".roottrace-fixture.json").read_text(encoding="utf-8"))
    return tuple(
        PathMapping(mapping["from"], mapping["to"]) for mapping in metadata["path_mappings"]
    )


MAPPINGS = _project_mappings()


class _NullLLMCallsRepository:
    async def insert(self, record: Any) -> str:
        return "call_1"


def _llm_gateway() -> LLMGateway:
    return LLMGateway(
        providers={"anthropic": AnthropicProvider(api_key=os.environ["RT_ANTHROPIC_API_KEY"])},
        routing=load_model_routing(),
        prompts=load_prompt_registry(),
        storage=InMemoryObjectStore(),
        db=_NullLLMCallsRepository(),
    )


@pytest.fixture(scope="module")
def gateway_transport() -> FixtureTransport:
    return FixtureTransport(FIXTURE_REPO)


@pytest.fixture(scope="module")
def fixture_tree(gateway_transport: FixtureTransport) -> RepoTree:
    return asyncio.run(
        gateway_transport.fetch_tree(FIXTURE_REPO_REF, gateway_transport.default_branch)
    )


@pytest.mark.parametrize("case_id", LIVE_CASE_IDS)
async def test_patch_runs_against_a_real_model_without_catastrophic_failure(
    case_id: str, gateway_transport: FixtureTransport, fixture_tree: RepoTree
) -> None:
    event = _payload(case_id)["events"][0]
    understanding = (await understand(event, mappings=MAPPINGS)).understanding
    candidates = await gather(
        gateway_transport,
        FIXTURE_REPO_REF,
        gateway_transport.default_branch,
        fixture_tree,
        understanding,
    )
    outcome = build_context_bundle(
        candidates,
        understanding,
        repo=FIXTURE_REPO_REF,
        ref=gateway_transport.default_branch,
        bundle_id=f"ctx_{case_id}",
    )
    if not isinstance(outcome, ContextBundle):
        pytest.skip(f"{case_id}: S5 produced insufficient_context, nothing for S6/S7 to work with")

    prompts = load_prompt_registry()
    reasoner = GatewayReasoner(gateway=_llm_gateway(), prompts=prompts, project_id="live-smoke")
    reason_outcome = await reason(outcome, understanding, reasoner=reasoner)
    if not reason_outcome.ok:
        pytest.skip(f"{case_id}: S6 produced insufficient_context, nothing for S7 to patch")
    assert reason_outcome.analysis is not None

    patcher = GatewayPatcher(gateway=_llm_gateway(), prompts=prompts, project_id="live-smoke")
    try:
        result = await patch(
            outcome,
            reason_outcome.analysis,
            patcher=patcher,
            patch_id=f"pat_{case_id}",
            base_commit=outcome.repository.commit_sha or "unknown",
        )
    except PatcherUnavailable as exc:
        # An honest terminal failure is an acceptable outcome for this smoke
        # test — it is not scoring correctness, only checking the mechanism
        # produced a real, specific reason either way.
        assert exc.error_code in ("RT-AI-0001", "RT-AI-0005", "RT-AI-0006")
        assert str(exc)
        return

    assert result.diff
    assert result.files_changed
    assert result.patch_id == f"pat_{case_id}"
