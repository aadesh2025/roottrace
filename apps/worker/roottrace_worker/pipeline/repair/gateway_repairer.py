"""The real `StructuredRepairer` (`03` §S9, T7.1) — calls `LLMGateway`
with `repair/v1.md` on the `fast` tier, per `06` §8.1's cost table ("S9
repair routing | fast | 2k / 0.5k | $0.002"). No bespoke correction-retry
ladder like `GatewayPatcher`'s: T5.1's generic three-attempt schema-repair
ladder (malformed JSON only) is all this stage needs, since there is no
S7-shaped semantic check to retry against — `instruction_delta` is prose,
not a diff that can be scope-violating or non-applying.

`{gate}`/`{gate_specific_instruction}` are filled in by code before the
prompt is ever assembled (`A2` §7: "Task layer (gate-specific instruction
is injected)") — the model never chooses either, only writes the
natural-language `instruction_delta` around them, same division of labour
`routing.py`'s module docstring describes."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from roottrace_worker.ai.contracts import CompletionRequest
from roottrace_worker.ai.errors import LLMError
from roottrace_worker.ai.gateway import LLMGateway
from roottrace_worker.ai.prompts.assembly import UntrustedBlock, assemble_prompt
from roottrace_worker.ai.prompts.registry import PromptRegistry
from roottrace_worker.pipeline.repair.extraction_schema import RepairReply
from roottrace_worker.pipeline.repair.repairer import RepairerUnavailable, RepairRequest

#: `03` §S9's own worked example, trimmed to what `RepairReply` actually
#: models — `repair_id`/`attempt`/`failed_gate`/`strategy`/
#: `reroute_to_stage`/`previous_attempts_summary` are all `routing.py`'s
#: or the caller's to assign, never the model's (`contracts.py`'s module
#: docstring).
_WORKED_EXAMPLE = json.dumps(
    {
        "instruction_delta": "Keep the typed exception, but update "
        "tests/test_quote.py to assert the new behaviour, and state in the PR "
        "description that quote.py's error surface changed."
    },
    indent=2,
)


def _error_block(request: RepairRequest) -> UntrustedBlock:
    exc = request.understanding.exception
    return UntrustedBlock(
        tag="original_error", attrs={"type": exc.type}, content=exc.message_normalized
    )


def _root_cause_block(request: RepairRequest) -> UntrustedBlock:
    root_cause = request.root_cause.root_cause
    payload = {"summary": root_cause.summary, "mechanism": root_cause.mechanism}
    return UntrustedBlock(tag="root_cause", attrs={}, content=json.dumps(payload, indent=2))


def _patch_block(request: RepairRequest) -> UntrustedBlock:
    return UntrustedBlock(
        tag="failed_patch",
        attrs={"patch_id": request.patch.patch_id},
        content=f"{request.patch.diff}\n\nEXPLANATION: {request.patch.explanation}",
    )


def _sandbox_output_block(request: RepairRequest) -> UntrustedBlock:
    return UntrustedBlock(
        tag="sandbox_output",
        attrs={"gate": request.failed_gate},
        content=json.dumps(dict(request.failure_detail), indent=2, default=str),
    )


def _previous_attempts_block(request: RepairRequest) -> UntrustedBlock | None:
    if not request.previous_attempts:
        return None
    payload = [attempt.model_dump() for attempt in request.previous_attempts]
    return UntrustedBlock(tag="previous_attempts", attrs={}, content=json.dumps(payload, indent=2))


def _build_untrusted_blocks(request: RepairRequest) -> tuple[UntrustedBlock, ...]:
    blocks = [
        _error_block(request),
        _root_cause_block(request),
        _patch_block(request),
        _sandbox_output_block(request),
    ]
    previous = _previous_attempts_block(request)
    if previous is not None:
        blocks.append(previous)
    return tuple(blocks)


class GatewayRepairer:
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

    async def repair(self, request: RepairRequest) -> Mapping[str, Any]:
        task_version = self._prompts.get("repair")
        system_text = self._prompts.get("system").text
        task_text = task_version.text.format(
            gate=request.failed_gate,
            gate_specific_instruction=request.gate_specific_instruction,
        )

        prompt = assemble_prompt(
            system=system_text,
            domain=None,
            task=task_text,
            untrusted_blocks=_build_untrusted_blocks(request),
            json_schema=RepairReply.model_json_schema(),
            worked_example=_WORKED_EXAMPLE,
        )

        try:
            result = await self._gateway.complete(
                CompletionRequest(
                    tier="fast",
                    prompt=prompt,
                    output_model=RepairReply,
                    project_id=self._project_id,
                    stage="repair",
                    prompt_version=task_version.prompt_version,
                    investigation_id=self._investigation_id,
                    pipeline_step_id=self._pipeline_step_id,
                    deterministic=True,
                )
            )
        except LLMError as exc:
            raise RepairerUnavailable(str(exc)) from exc

        return {"instruction_delta": result.output.instruction_delta}
