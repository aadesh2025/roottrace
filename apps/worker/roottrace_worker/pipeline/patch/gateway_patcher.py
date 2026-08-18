"""The real `StructuredPatcher` (`03` §S7, T5.4) — calls `LLMGateway` with
`patch/v4.md` on the `reasoning-a` tier.

**Owns the same shape of second retry ladder `GatewayReasoner` does, for the
same reason.** T5.1's gateway retries on JSON-schema violation only; `03`
§S7's own retries ("on scope violation ... on non-applying diff: one retry,
then a registered failure code") are semantic checks against the bundle
and the fix strategy, which only this module has. One difference from S6:
where S6's ladder resumes from a boolean "did the primary finding survive",
S7's `validate_patch` already tells us *which* of the two registered
failure codes applies, so the correction prompt can be specific about
whether the problem was scope or applicability."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from roottrace_worker.ai.contracts import CompletionRequest, LLMResult
from roottrace_worker.ai.errors import LLMError
from roottrace_worker.ai.gateway import LLMGateway
from roottrace_worker.ai.prompts.assembly import UntrustedBlock, assemble_prompt
from roottrace_worker.ai.prompts.registry import PromptRegistry
from roottrace_worker.pipeline.patch.extraction_schema import PatchReply
from roottrace_worker.pipeline.patch.patcher import PatchErrorCode, PatcherUnavailable, PatchRequest
from roottrace_worker.pipeline.patch.validate import PatchValidation, validate_patch
from roottrace_worker.pipeline.reason.contracts import RootCauseAnalysis
from roottrace_worker.pipeline.retrieve.bundle import BundleFile, ContextBundle

#: `03` §S7's worked example, trimmed to what `PatchReply` actually models —
#: `patch_id`/`base_commit`/`files_changed`/`scope_warning`/`model`/
#: `prompt_version` are all ours to assign or compute, never the model's, so
#: none of them appear here (`contracts.py`'s module docstring).
_WORKED_EXAMPLE = json.dumps(
    {
        "diff": "--- a/clients/tax_client.py\n+++ b/clients/tax_client.py\n"
        "@@ -36,9 +36,12 @@\n ...",
        "explanation": "TaxClient.get_rate now raises a typed TaxServiceUnavailable "
        "instead of returning None, preserving the existing warning log. "
        "calculate_total catches it and re-raises as a 503 with a clear message rather "
        "than producing a TypeError. A regression test asserts the new behaviour under a "
        "mocked 503.",
        "regression_test": {
            "repo_path": "tests/test_checkout.py",
            "test_name": "test_calculate_total_raises_when_tax_service_unavailable",
            "reproduces_original_error": True,
            "expected_before_patch": "fail",
            "expected_after_patch": "pass",
        },
        "risk_assessment": {
            "level": "low",
            "breaking_change": True,
            "breaking_change_note": "get_rate's contract changes from returning Optional "
            "to raising. services/quote.py::estimate_total also calls it and will now "
            "propagate the error rather than fail with TypeError.",
            "touches_auth": False,
            "touches_data_migration": False,
            "touches_public_api": False,
        },
        "alternatives_considered": [
            {
                "approach": "Default tax to 0 when unavailable",
                "rejected_because": "Silently under-charges customers; converts a loud "
                "failure into a financial defect.",
            }
        ],
    },
    indent=2,
)


def _file_block(file: BundleFile) -> UntrustedBlock:
    attrs = {"path": file.repo_path, "lines": f"{file.line_range[0]}-{file.line_range[1]}"}
    if file.blame is not None:
        attrs["sha"] = file.blame.commit.sha
    return UntrustedBlock(tag="file", attrs=attrs, content=file.content)


def _test_blocks(bundle: ContextBundle) -> tuple[UntrustedBlock, ...]:
    return tuple(
        UntrustedBlock(tag="test", attrs={"path": match.repo_path}, content=match.content)
        for match in bundle.tests.found
    )


def _fix_strategy_block(analysis: RootCauseAnalysis) -> UntrustedBlock:
    fix_strategy = analysis.fix_strategy
    payload = {
        "root_cause_summary": analysis.root_cause.summary,
        "root_cause_mechanism": analysis.root_cause.mechanism,
        "approach": fix_strategy.approach,
        "files_to_modify": list(fix_strategy.files_to_modify),
        "must_not_modify": list(fix_strategy.must_not_modify),
        "considerations": list(fix_strategy.considerations),
        "regression_test_needed": fix_strategy.regression_test_needed,
        "regression_test_description": fix_strategy.regression_test_description,
    }
    return UntrustedBlock(tag="fix_strategy", attrs={}, content=json.dumps(payload, indent=2))


def _build_untrusted_blocks(request: PatchRequest) -> tuple[UntrustedBlock, ...]:
    return (
        _fix_strategy_block(request.analysis),
        *(_file_block(file) for file in request.bundle.files),
        *_test_blocks(request.bundle),
    )


_CORRECTION_PREAMBLE = (
    "CORRECTION NEEDED. Your previous diff was rejected: {reason}\n\n"
    "Modify only files in fix_strategy.files_to_modify plus your own declared regression "
    "test path. Never touch a forbidden path, a file in fix_strategy.must_not_modify, or "
    "an existing test. The diff must apply cleanly to the exact retrieved content shown "
    "below, with correct line numbers. If a regression test is required, produce one.\n\n"
    "YOUR PREVIOUS RESPONSE:\n{previous_reply}\n\n"
)


def _failure_reason(validation: PatchValidation) -> str:
    if validation.scope_violation is not None:
        return validation.scope_violation.reason
    if validation.applicability_failure is not None:
        return validation.applicability_failure.reason
    return "validation failed for an unspecified reason"


def _error_code(validation: PatchValidation) -> PatchErrorCode:
    return "RT-AI-0005" if validation.scope_violation is not None else "RT-AI-0006"


class GatewayPatcher:
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

    async def patch(self, request: PatchRequest) -> Mapping[str, Any]:
        task_version = self._prompts.get("patch")
        system_text = self._prompts.get("system").text
        blocks = _build_untrusted_blocks(request)

        prompt = assemble_prompt(
            system=system_text,
            domain=None,
            task=task_version.text,
            untrusted_blocks=blocks,
            json_schema=PatchReply.model_json_schema(),
            worked_example=_WORKED_EXAMPLE,
        )

        result = await self._complete(prompt, task_version.prompt_version)
        validation = validate_patch(result.output, bundle=request.bundle, analysis=request.analysis)
        if validation.ok:
            return _with_call_metadata(result.output, result)

        reason = _failure_reason(validation)
        correction_task = (
            _CORRECTION_PREAMBLE.format(
                reason=reason, previous_reply=result.output.model_dump_json()
            )
            + task_version.text
        )
        correction_prompt = assemble_prompt(
            system=system_text,
            domain=None,
            task=correction_task,
            untrusted_blocks=blocks,
            json_schema=PatchReply.model_json_schema(),
            worked_example=_WORKED_EXAMPLE,
        )

        retry_result = await self._complete(correction_prompt, task_version.prompt_version)
        retry_validation = validate_patch(
            retry_result.output, bundle=request.bundle, analysis=request.analysis
        )
        if not retry_validation.ok:
            raise PatcherUnavailable(
                f"{_failure_reason(retry_validation)} (failed twice)",
                error_code=_error_code(retry_validation),
            )
        return _with_call_metadata(retry_result.output, retry_result)

    async def _complete(self, prompt: Any, prompt_version: str) -> LLMResult[PatchReply]:
        try:
            return await self._gateway.complete(
                CompletionRequest(
                    tier="reasoning-a",
                    prompt=prompt,
                    output_model=PatchReply,
                    project_id=self._project_id,
                    stage="patch",
                    prompt_version=prompt_version,
                    investigation_id=self._investigation_id,
                    pipeline_step_id=self._pipeline_step_id,
                )
            )
        except LLMError as exc:
            raise PatcherUnavailable(str(exc), error_code="RT-AI-0001") from exc


def _with_call_metadata(reply: PatchReply, result: LLMResult[PatchReply]) -> dict[str, Any]:
    """Same rule as `reason`'s `_with_call_metadata`: identity and billing
    facts come from the gateway's own record of the call, never from the
    model's JSON."""
    payload = reply.model_dump()
    payload["model"] = result.model
    payload["prompt_version"] = result.prompt_version
    payload["tokens"] = {"prompt": result.tokens_in, "completion": result.tokens_out}
    return payload
