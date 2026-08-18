"""The real `StructuredReasoner` (`03` §S6, T5.3) — calls `LLMGateway` with
`reason/v3.md` on the `reasoning-a` tier, `03` §S6's "most expensive call
in the pipeline; it is where the value is".

**Owns a second retry ladder the gateway does not and should not know
about.** T5.1's gateway retries on *schema* failure (`06` §4.1) — a JSON
Schema violation, checkable with no domain knowledge. `03` §S6's own retry
("if the primary root-cause finding fails validation, one correction
retry, then terminal `insufficient_context`") is a *semantic* failure —
whether a citation actually appears in the retrieved bundle — that only
this module, which has the bundle, can check. The gateway already ran its
own schema ladder before this module ever sees a reply; this ladder starts
from there."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from roottrace_worker.ai.contracts import CompletionRequest, LLMResult
from roottrace_worker.ai.errors import LLMError
from roottrace_worker.ai.gateway import LLMGateway
from roottrace_worker.ai.prompts.assembly import UntrustedBlock, assemble_prompt
from roottrace_worker.ai.prompts.registry import PromptRegistry
from roottrace_worker.pipeline.reason.extraction_schema import ReasonReply
from roottrace_worker.pipeline.reason.reasoner import ReasonerUnavailable, ReasonRequest
from roottrace_worker.pipeline.reason.validate import (
    fix_strategy_is_grounded,
    primary_finding_survives,
    validate_reasoning_chain,
)
from roottrace_worker.pipeline.retrieve.bundle import BundleFile, ContextBundle

#: `03` §S6's worked example, trimmed to what `ReasonReply` models.
_WORKED_EXAMPLE = json.dumps(
    {
        "root_cause": {
            "summary": "TaxClient.get_rate() catches HTTPError and returns None on any "
            "non-200 response, so a 503 silently yields None.",
            "mechanism": "tax-service returns 503 -> TaxClient.get_rate catches "
            "httpx.HTTPStatusError at clients/tax_client.py:41 and returns None -> "
            "calculate_total receives None at services/checkout.py:138 with no guard.",
            "category": "unhandled_error_path",
        },
        "reasoning_chain": [
            {
                "step": 1,
                "type": "observe",
                "statement": "The exception is raised at services/checkout.py:142.",
                "evidence": [
                    {
                        "kind": "file",
                        "repo_path": "services/checkout.py",
                        "line_range": [140, 143],
                        "excerpt": "subtotal = base_price + tax_amount",
                    }
                ],
            },
            {
                "step": 2,
                "type": "hypothesise",
                "statement": "get_rate returns None under some condition.",
                "prior": 0.7,
            },
            {
                "step": 3,
                "type": "conclude",
                "statement": "Root cause is the broken error contract in TaxClient.get_rate.",
            },
        ],
        "fix_strategy": {
            "approach": "Restore error propagation in TaxClient.get_rate.",
            "files_to_modify": ["clients/tax_client.py", "services/checkout.py"],
            "regression_test_needed": True,
        },
        "self_assessed_confidence": 0.88,
    },
    indent=2,
)


def _file_block(file: BundleFile) -> UntrustedBlock:
    attrs = {"path": file.repo_path, "lines": f"{file.line_range[0]}-{file.line_range[1]}"}
    if file.blame is not None:
        attrs["sha"] = file.blame.commit.sha
    return UntrustedBlock(tag="file", attrs=attrs, content=file.content)


def _breadcrumb_blocks(request: ReasonRequest) -> tuple[UntrustedBlock, ...]:
    blocks = []
    for index, breadcrumb in enumerate(request.breadcrumbs):
        attrs = {"index": str(index)}
        timestamp = breadcrumb.get("ts") or breadcrumb.get("timestamp")
        if isinstance(timestamp, str):
            attrs["ts"] = timestamp
        blocks.append(UntrustedBlock(tag="breadcrumb", attrs=attrs, content=json.dumps(breadcrumb)))
    return tuple(blocks)


def _commit_blocks(bundle: ContextBundle) -> tuple[UntrustedBlock, ...]:
    blocks = []
    seen: set[str] = set()
    commits = list(bundle.history.recent_commits)
    if bundle.history.blame_commit is not None:
        commits.append(bundle.history.blame_commit)
    for commit in commits:
        if commit.sha in seen:
            continue
        seen.add(commit.sha)
        blocks.append(
            UntrustedBlock(
                tag="commit",
                attrs={"sha": commit.sha, "date": commit.date.isoformat()},
                content=commit.message,
            )
        )
    return tuple(blocks)


def _test_blocks(bundle: ContextBundle) -> tuple[UntrustedBlock, ...]:
    return tuple(
        UntrustedBlock(tag="test", attrs={"path": match.repo_path}, content=match.content)
        for match in bundle.tests.found
    )


def _error_context_block(request: ReasonRequest) -> UntrustedBlock:
    understanding = request.understanding
    payload = {
        "exception": {
            "type": understanding.exception.type,
            "family": understanding.exception.family.value,
            "message": understanding.exception.message_normalized,
        },
        "entry_point": understanding.entry_point.model_dump()
        if understanding.entry_point
        else None,
        "failure_point": understanding.failure_point.model_dump()
        if understanding.failure_point
        else None,
        "implicated_symbols": list(understanding.implicated_symbols),
        "initial_hypotheses": [h.statement for h in understanding.initial_hypotheses],
    }
    return UntrustedBlock(tag="error_context", attrs={}, content=json.dumps(payload, indent=2))


def _build_untrusted_blocks(request: ReasonRequest) -> tuple[UntrustedBlock, ...]:
    return (
        _error_context_block(request),
        *(_file_block(file) for file in request.bundle.files),
        *_breadcrumb_blocks(request),
        *_commit_blocks(request.bundle),
        *_test_blocks(request.bundle),
    )


_CORRECTION_PREAMBLE = (
    "CORRECTION NEEDED. Your previous response's root-cause conclusion could not be "
    "verified: {reasons}\n\n"
    "Every citation must reference content that actually appears in the retrieved "
    "context below — a file path and line range whose excerpt matches the source "
    "exactly, a breadcrumb index that exists, or a commit sha that exists in the "
    "history shown. Do not invent evidence. If you cannot support a conclusion with "
    "real evidence, say so using the same fields, or narrow the conclusion to what the "
    "evidence actually shows.\n\n"
    "YOUR PREVIOUS RESPONSE:\n{previous_reply}\n\n"
)


class GatewayReasoner:
    def __init__(
        self,
        *,
        gateway: LLMGateway,
        prompts: PromptRegistry,
        project_id: str,
        investigation_id: str | None = None,
        pipeline_step_id: str | None = None,
    ) -> None:
        self._gateway = gateway
        self._prompts = prompts
        self._project_id = project_id
        self._investigation_id = investigation_id
        self._pipeline_step_id = pipeline_step_id

    async def reason(self, request: ReasonRequest) -> Mapping[str, Any]:
        task_version = self._prompts.get("reason")
        system_text = self._prompts.get("system").text
        blocks = _build_untrusted_blocks(request)

        prompt = assemble_prompt(
            system=system_text,
            domain=None,
            task=task_version.text,
            untrusted_blocks=blocks,
            json_schema=ReasonReply.model_json_schema(),
            worked_example=_WORKED_EXAMPLE,
        )

        result = await self._complete(prompt, task_version.prompt_version)
        reply = result.output
        kept_steps, _dropped = validate_reasoning_chain(
            reply.reasoning_chain, bundle=request.bundle, breadcrumb_count=len(request.breadcrumbs)
        )

        if primary_finding_survives(kept_steps, reply=reply, bundle=request.bundle):
            return _with_call_metadata(reply, result)

        reasons = _failure_reasons(kept_steps, reply=reply, bundle=request.bundle)
        correction_task = (
            _CORRECTION_PREAMBLE.format(
                reasons="; ".join(reasons), previous_reply=reply.model_dump_json()
            )
            + task_version.text
        )
        correction_prompt = assemble_prompt(
            system=system_text,
            domain=None,
            task=correction_task,
            untrusted_blocks=blocks,
            json_schema=ReasonReply.model_json_schema(),
            worked_example=_WORKED_EXAMPLE,
        )

        retry_result = await self._complete(correction_prompt, task_version.prompt_version)
        retry_reply = retry_result.output
        retry_kept_steps, _retry_dropped = validate_reasoning_chain(
            retry_reply.reasoning_chain,
            bundle=request.bundle,
            breadcrumb_count=len(request.breadcrumbs),
        )
        if not primary_finding_survives(retry_kept_steps, reply=retry_reply, bundle=request.bundle):
            raise ReasonerUnavailable("evidence binding failed twice: " + "; ".join(reasons))
        return _with_call_metadata(retry_reply, retry_result)

    async def _complete(self, prompt: Any, prompt_version: str) -> LLMResult[ReasonReply]:
        try:
            return await self._gateway.complete(
                CompletionRequest(
                    tier="reasoning-a",
                    prompt=prompt,
                    output_model=ReasonReply,
                    project_id=self._project_id,
                    stage="reason",
                    prompt_version=prompt_version,
                    investigation_id=self._investigation_id,
                    pipeline_step_id=self._pipeline_step_id,
                )
            )
        except LLMError as exc:
            raise ReasonerUnavailable(str(exc)) from exc


def _with_call_metadata(reply: ReasonReply, result: LLMResult[ReasonReply]) -> dict[str, Any]:
    """`03` §S6's `model`/`prompt_version`/`tokens` fields come from the real
    `LLMResult` the gateway returned, never from the model's own JSON — a
    model has no reliable way to know its own identity string or the exact
    token counts a provider billed for the call, so asking it to self-report
    either would just be a second, less trustworthy copy of data the caller
    already has for free."""
    payload = reply.model_dump()
    payload["model"] = result.model
    payload["prompt_version"] = result.prompt_version
    payload["tokens"] = {"prompt": result.tokens_in, "completion": result.tokens_out}
    return payload


def _failure_reasons(kept_steps: Any, *, reply: ReasonReply, bundle: ContextBundle) -> list[str]:
    reasons = []
    if not any(step.type == "conclude" for step in kept_steps):
        reasons.append("no `conclude`-type reasoning step survived evidence binding")
    if not fix_strategy_is_grounded(reply.fix_strategy, bundle=bundle):
        unretrieved = sorted(
            set(reply.fix_strategy.files_to_modify) - {f.repo_path for f in bundle.files}
        )
        reasons.append(f"fix_strategy.files_to_modify names file(s) never retrieved: {unretrieved}")
    return reasons or ["the primary finding did not survive evidence binding"]
