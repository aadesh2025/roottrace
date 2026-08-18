"""Live smoke test for S6 `reason` against real fixture cases with a real
model — skipped unless a real API key is present, same pattern as
`test_ai_provider_live.py`.

**Not an accuracy measurement.** `15` T5.3's own `≥20/25 fixtures identify
the correct root-cause file` bar is a corpus-wide statistical claim that
needs the full 25-case corpus, multiple runs, and a real evaluation
harness to measure honestly — that is `T10.1`, Phase 15, exactly where
`15` already puts it (see `15` T5.3's own accept-criteria note). Scripting
`FakeProvider` to "pass" 20/25 would prove nothing about a real model's
capability, and building a second, smaller eval harness inside this ticket
would just duplicate T10.1's job ahead of time, with less rigour.

What this file *does* check, deliberately narrow: run `reason()` against a
handful of real fixture cases, through a real retrieval pipeline, with a
real model call, and confirm the mechanism does not fall over — no
exceptions, a well-formed `RootCauseAnalysis` or an honest
`insufficient_context`, evidence that actually binds. Enough to catch
something catastrophically wrong before T5.4 (patch generation) is built
on top of this stage, not enough to claim the accuracy bar is met."""

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
from roottrace_worker.pipeline.reason import GatewayReasoner, reason
from roottrace_worker.pipeline.retrieve.bundle import ContextBundle, InsufficientContext
from roottrace_worker.pipeline.retrieve.ranking import build_context_bundle
from roottrace_worker.pipeline.retrieve.strategies import gather
from roottrace_worker.pipeline.understand import PathMapping, understand
from roottrace_worker.pipeline.understand.contracts import ErrorUnderstanding

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

#: A small, deliberately diverse subset — not all 25. One thin single-file
#: case (`key-error-01`, T4.4's own worst-case shape), one multi-file case,
#: one integration/external case, and one control, so a mechanism failure
#: specific to a particular bundle shape has a chance of showing up.
LIVE_CASE_IDS: tuple[str, ...] = (
    "null-prop-01",
    "type-mismatch-01",
    "key-error-01",
    "external-01",
    "config-01",
    "regression-01",
    "unfixable-01",
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


async def _build_bundle(
    case_id: str, gateway_transport: FixtureTransport, tree: RepoTree
) -> tuple[ContextBundle | InsufficientContext, ErrorUnderstanding]:
    event = _payload(case_id)["events"][0]
    understanding = (await understand(event, mappings=MAPPINGS)).understanding
    candidates = await gather(
        gateway_transport, FIXTURE_REPO_REF, gateway_transport.default_branch, tree, understanding
    )
    outcome = build_context_bundle(
        candidates,
        understanding,
        repo=FIXTURE_REPO_REF,
        ref=gateway_transport.default_branch,
        bundle_id=f"ctx_{case_id}",
    )
    return outcome, understanding


@pytest.fixture(scope="module")
def gateway_transport() -> FixtureTransport:
    return FixtureTransport(FIXTURE_REPO)


@pytest.fixture(scope="module")
def fixture_tree(gateway_transport: FixtureTransport) -> RepoTree:
    return asyncio.run(
        gateway_transport.fetch_tree(FIXTURE_REPO_REF, gateway_transport.default_branch)
    )


@pytest.mark.parametrize("case_id", LIVE_CASE_IDS)
async def test_reason_runs_against_a_real_model_without_catastrophic_failure(
    case_id: str, gateway_transport: FixtureTransport, fixture_tree: RepoTree
) -> None:
    outcome, understanding = await _build_bundle(case_id, gateway_transport, fixture_tree)
    if not isinstance(outcome, ContextBundle):
        pytest.skip(f"{case_id}: S5 produced insufficient_context, nothing for S6 to reason over")

    llm_gateway = LLMGateway(
        providers={"anthropic": AnthropicProvider(api_key=os.environ["RT_ANTHROPIC_API_KEY"])},
        routing=load_model_routing(),
        prompts=load_prompt_registry(),
        storage=InMemoryObjectStore(),
        db=_NullLLMCallsRepository(),
    )
    reasoner = GatewayReasoner(
        gateway=llm_gateway, prompts=load_prompt_registry(), project_id="live-smoke"
    )

    result = await reason(outcome, understanding, reasoner=reasoner)

    if result.ok:
        assert result.analysis is not None
        assert result.analysis.root_cause.summary
        assert result.analysis.reasoning_chain
        # Every surviving step's evidence was already bound by `reason()` —
        # this just confirms at least one real citation exists, not zero.
        assert any(step.evidence for step in result.analysis.reasoning_chain)
    else:
        # An honest insufficient_context is an acceptable outcome for this
        # smoke test — it is not scoring correctness, only checking the
        # mechanism produced a well-formed terminal state either way.
        assert result.insufficient is not None
        assert result.insufficient.explanation
