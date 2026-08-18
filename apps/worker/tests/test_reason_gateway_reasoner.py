"""`GatewayReasoner` (T5.3) — the real `StructuredReasoner`, exercised
end-to-end through `reason(...)` with a `FakeProvider` standing in for the
model. Mirrors `test_understand_gateway_extractor.py`'s shape: this proves
the prompt system and the gateway compose correctly for S6, the same way
that file proved it for S4.
"""

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
from roottrace_worker.pipeline.reason import GatewayReasoner, reason
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

_PROMPTS = load_prompt_registry()

FILE_CONTENT = (
    "def calculate_total():\n    subtotal = base_price + tax_amount\n    return subtotal\n"
)

COMMIT = Commit(
    sha="8a3f1c2e" + "0" * 32,
    message="refactor: extract tax lookup",
    author=Actor(name="d", email="d@x.io"),
    date=datetime(2026, 7, 25, tzinfo=UTC),
)

VALID_REPLY = json.dumps(
    {
        "root_cause": {
            "summary": "TaxClient swallows errors",
            "mechanism": "get_rate catches HTTPError and returns None",
            "category": "unhandled_error_path",
        },
        "reasoning_chain": [
            {
                "step": 1,
                "type": "observe",
                "statement": "The exception is raised in calculate_total.",
                "evidence": [
                    {
                        "kind": "file",
                        "repo_path": "services/checkout.py",
                        "line_range": [1, 2],
                        "excerpt": "def calculate_total():",
                    }
                ],
            },
            {
                "step": 2,
                "type": "conclude",
                "statement": "Root cause is the broken error contract.",
                "evidence": [{"kind": "commit", "sha": COMMIT.sha}],
            },
        ],
        "fix_strategy": {
            "approach": "Restore error propagation.",
            "files_to_modify": ["services/checkout.py"],
            "regression_test_needed": True,
        },
        "self_assessed_confidence": 0.85,
    }
)

UNBOUND_REPLY = json.dumps(
    {
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
                        "excerpt": "this excerpt was never in the file",
                    }
                ],
            }
        ],
        "fix_strategy": {"approach": "a", "files_to_modify": ["services/checkout.py"]},
        "self_assessed_confidence": 0.5,
    }
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
        history=BundleHistory(blame_commit=COMMIT, recent_commits=(COMMIT,)),
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


def _reasoner(
    provider: FakeProvider, *, db: InMemoryLLMCallsRepository | None = None
) -> GatewayReasoner:
    gateway = LLMGateway(
        providers={"anthropic": provider},
        routing=parse_model_routing(_routing_doc()),
        prompts=_PROMPTS,
        storage=InMemoryObjectStore(),
        db=db or InMemoryLLMCallsRepository(),
        sleep=_noop_sleep,
    )
    return GatewayReasoner(gateway=gateway, prompts=_PROMPTS, project_id="proj_1")


async def test_a_well_evidenced_reply_produces_a_root_cause_analysis() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    outcome = await reason(_bundle(), _understanding(), reasoner=_reasoner(provider))

    assert outcome.ok
    assert outcome.analysis is not None
    assert outcome.analysis.root_cause.summary == "TaxClient swallows errors"
    assert len(outcome.analysis.reasoning_chain) == 2
    assert outcome.analysis.fix_strategy.files_to_modify == ("services/checkout.py",)


async def test_the_call_reaches_the_provider_with_the_assembled_prompt() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    await reason(_bundle(), _understanding(), reasoner=_reasoner(provider))

    assert len(provider.calls) == 1
    call = provider.calls[0]
    assert "root cause" in call.system.lower() or "ROOT CAUSE" in call.system
    assert "<untrusted_context>" in call.user
    assert "def calculate_total" in call.user  # the bundle file, fenced as data
    assert call.model == "claude-sonnet-5"  # reasoning-a tier


async def test_model_and_tokens_come_from_the_real_llm_result_not_the_reply() -> None:
    """The model is never asked to self-report its own identity or token
    usage — `ReasonReply` has no such fields. `GatewayReasoner` injects the
    real values from `LLMResult`."""
    provider = FakeProvider(
        name="anthropic",
        outcomes=[ScriptedSuccess(VALID_REPLY, tokens_in=19_000, tokens_out=2_100)],
    )
    outcome = await reason(_bundle(), _understanding(), reasoner=_reasoner(provider))

    assert outcome.analysis is not None
    # `LLMResult.model` reflects the routing tier's configured model name
    # (`06` §2.2), not `ProviderResponse.model` — a provider may echo back a
    # more specific string (e.g. a dated snapshot); the tier config is what
    # `llm_calls` and this analysis both consistently record.
    assert outcome.analysis.model == "claude-sonnet-5"
    assert outcome.analysis.tokens.prompt == 19_000
    assert outcome.analysis.tokens.completion == 2_100


async def test_unbound_evidence_triggers_a_correction_retry() -> None:
    provider = FakeProvider(
        name="anthropic", outcomes=[ScriptedSuccess(UNBOUND_REPLY), ScriptedSuccess(VALID_REPLY)]
    )
    outcome = await reason(_bundle(), _understanding(), reasoner=_reasoner(provider))

    assert len(provider.calls) == 2
    # The correction instruction is part of L3 (task), which `assemble_prompt`
    # places in `system`, not `user` (L4/L5) — see `06` §2.4's L1-L3/L4-L5 split.
    assert "CORRECTION NEEDED" in provider.calls[1].system
    assert outcome.ok
    assert outcome.analysis is not None
    assert outcome.analysis.root_cause.summary == "TaxClient swallows errors"


async def test_unbound_evidence_on_both_attempts_terminates_as_insufficient_context() -> None:
    provider = FakeProvider(
        name="anthropic", outcomes=[ScriptedSuccess(UNBOUND_REPLY), ScriptedSuccess(UNBOUND_REPLY)]
    )
    outcome = await reason(_bundle(), _understanding(), reasoner=_reasoner(provider))

    assert not outcome.ok
    assert outcome.insufficient is not None
    assert "evidence binding" in outcome.insufficient.explanation
    assert len(provider.calls) == 2


async def test_an_ungrounded_fix_strategy_also_triggers_a_correction_retry() -> None:
    """A `conclude` step with real evidence still fails the primary-finding
    check if `fix_strategy.files_to_modify` names a file S5 never
    retrieved (`06` §4.2's `files_to_modify` ⊆ retrieved paths check) —
    the other of `_failure_reasons`'s two branches, distinct from "no
    conclude step survived"."""
    ungrounded_reply = json.dumps(
        {
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
                            "excerpt": "def calculate_total():",
                        }
                    ],
                }
            ],
            "fix_strategy": {"approach": "a", "files_to_modify": ["services/never_retrieved.py"]},
            "self_assessed_confidence": 0.5,
        }
    )
    provider = FakeProvider(
        name="anthropic", outcomes=[ScriptedSuccess(ungrounded_reply), ScriptedSuccess(VALID_REPLY)]
    )
    outcome = await reason(_bundle(), _understanding(), reasoner=_reasoner(provider))

    assert len(provider.calls) == 2
    assert "never retrieved" in provider.calls[1].system
    assert outcome.ok


async def test_breadcrumbs_are_fenced_as_data_when_present() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    breadcrumbs = [{"ts": "2026-08-04T09:14:22.200Z", "message": "GET tax-service/rate -> 503"}]
    await reason(_bundle(), _understanding(), breadcrumbs=breadcrumbs, reasoner=_reasoner(provider))

    call = provider.calls[0]
    assert "<breadcrumb" in call.user
    assert "tax-service/rate" in call.user


async def test_a_file_with_blame_includes_the_introducing_commit_sha_in_the_prompt() -> None:
    from roottrace_worker.pipeline.retrieve.bundle import BlameInfo

    bundle = _bundle()
    blamed_file = bundle.files[0].model_copy(update={"blame": BlameInfo(line=1, commit=COMMIT)})
    bundle = bundle.model_copy(update={"files": (blamed_file,)})

    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    await reason(bundle, _understanding(), reasoner=_reasoner(provider))

    call = provider.calls[0]
    assert f'sha="{COMMIT.sha}"' in call.user


async def test_a_gateway_failure_terminates_as_insufficient_context() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedFailure("server_error")])
    outcome = await reason(_bundle(), _understanding(), reasoner=_reasoner(provider))

    assert not outcome.ok
    assert outcome.insufficient is not None
    assert outcome.insufficient.bundle_id == "ctx_1"


async def test_every_call_is_billed_and_recorded() -> None:
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(VALID_REPLY)])
    db = InMemoryLLMCallsRepository()
    await reason(_bundle(), _understanding(), reasoner=_reasoner(provider, db=db))

    assert len(db.records) == 1
    assert db.records[0].stage == "reason"
    assert db.records[0].tier == "reasoning-a"


async def test_a_correction_retry_writes_two_llm_calls_rows() -> None:
    provider = FakeProvider(
        name="anthropic", outcomes=[ScriptedSuccess(UNBOUND_REPLY), ScriptedSuccess(VALID_REPLY)]
    )
    db = InMemoryLLMCallsRepository()
    await reason(_bundle(), _understanding(), reasoner=_reasoner(provider, db=db))

    assert len(db.records) == 2


async def test_a_step_with_unbound_evidence_is_dropped_but_does_not_fail_the_whole_analysis() -> (
    None
):
    """Only the *primary* finding gates retry (`03` §S6). A non-primary step
    with bad evidence is silently dropped, not fatal, as long as a
    `conclude`-type step with real evidence still survives."""
    reply = json.dumps(
        {
            "root_cause": {"summary": "s", "mechanism": "m", "category": "other"},
            "reasoning_chain": [
                {
                    "step": 1,
                    "type": "observe",
                    "statement": "a side observation with fabricated evidence",
                    "evidence": [
                        {
                            "kind": "file",
                            "repo_path": "services/checkout.py",
                            "line_range": [1, 2],
                            "excerpt": "never in the file",
                        }
                    ],
                },
                {
                    "step": 2,
                    "type": "conclude",
                    "statement": "the real conclusion",
                    "evidence": [
                        {
                            "kind": "file",
                            "repo_path": "services/checkout.py",
                            "line_range": [1, 2],
                            "excerpt": "def calculate_total():",
                        }
                    ],
                },
            ],
            "fix_strategy": {"approach": "a", "files_to_modify": ["services/checkout.py"]},
            "self_assessed_confidence": 0.5,
        }
    )
    provider = FakeProvider(name="anthropic", outcomes=[ScriptedSuccess(reply)])
    outcome = await reason(_bundle(), _understanding(), reasoner=_reasoner(provider))

    assert outcome.ok
    assert outcome.analysis is not None
    assert len(outcome.analysis.reasoning_chain) == 1
    assert outcome.analysis.reasoning_chain[0].type == "conclude"
    assert len(outcome.dropped_claims) == 1
