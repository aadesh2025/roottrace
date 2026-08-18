"""The real `StructuredExtractor` (`03` §S4 step 2, T5.2) — what
`UnavailableExtractor` (T4.1) was always waiting for. Calls `LLMGateway`
with `understand/v3.md`; nothing else in the stage changes, exactly as
`03` §S4's implementation note and `extractor.py`'s own docstring promised.

**Not wired to a running investigation yet.** `GatewayExtractor` is
constructed with the `project_id`/`investigation_id`/`pipeline_step_id`
a live call needs — the ARQ task that would create one per investigation
and call `understand(event, extractor=GatewayExtractor(...))` does not
exist yet (pipeline orchestration is out of T5.2's scope, and out of every
ticket built so far). This class is real and tested against `FakeProvider`
end-to-end through the gateway; only its caller is still missing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from roottrace_worker.ai.contracts import CompletionRequest
from roottrace_worker.ai.errors import LLMError
from roottrace_worker.ai.gateway import LLMGateway
from roottrace_worker.ai.prompts.assembly import UntrustedBlock, assemble_prompt
from roottrace_worker.ai.prompts.registry import PromptRegistry
from roottrace_worker.pipeline.understand.extraction_schema import UnderstandExtractionReply
from roottrace_worker.pipeline.understand.extractor import ExtractionRequest, ExtractorUnavailable
from roottrace_worker.pipeline.understand.taxonomy import PROFILES

#: `03` §S4's worked example, trimmed to what `UnderstandExtractionReply`
#: actually models — `A2` §1's "one worked example" rule, L5.
_WORKED_EXAMPLE = json.dumps(
    {
        "exception": {"family": "null_undefined"},
        "frames": [{"index": 0, "confidence": 0.95}],
        "implicated_symbols": ["calculate_total", "get_rate"],
        "initial_hypotheses": [
            {
                "statement": "tax_amount is None because the tax service returned 503 and "
                "the error path returns None instead of raising",
                "prior": 0.65,
                "evidence_needed": ["the tax service client implementation"],
            }
        ],
        "retrieval_plan": {
            "must_fetch": ["services/checkout.py"],
            "should_fetch_by_symbol": ["get_tax_rate"],
            "breadcrumb_signal": "GET tax-service/rate returned 503 141 ms before the error",
        },
        "notes": "The breadcrumb showing a 503 immediately before the failure is the "
        "strongest available signal.",
        "extraction_confidence": 0.91,
    },
    indent=2,
)


def render_domain_layer(_language: str | None) -> str:
    """`06` §3.1: "L2 DOMAIN ... selected by S4 taxonomy". `A2` §3 calls for
    this "filtered to the detected language" — V1's corpus and synthetic
    repo are Python-only throughout (`CLAUDE.md`), and `taxonomy.PROFILES`
    carries no per-language variants to filter by, so every call renders
    the whole table. `_language` is accepted (not dropped from the
    signature) so the parameter is already in place for when a second
    language's idioms exist to filter between."""
    rows = "\n".join(
        f"| {family.value} | {profile.cause_class} | {profile.retrieval_hint} |"
        for family, profile in PROFILES.items()
    )
    return (
        "EXCEPTION TAXONOMY\n\n"
        "| Family | Typical cause class | Retrieval hint |\n"
        "|---|---|---|\n" + rows
    )


def _exception_block(request: ExtractionRequest) -> UntrustedBlock:
    return UntrustedBlock(
        tag="exception",
        attrs={"type": request.exception_type, "family": request.family.value},
        content=request.exception_message,
    )


def _frames_block(request: ExtractionRequest) -> UntrustedBlock:
    payload = [
        {
            "index": frame.index,
            "raw_path": frame.raw_path,
            "repo_path": frame.repo_path,
            "line": frame.line,
            "function": frame.function,
            "in_app": frame.in_app,
            "confidence": frame.confidence,
        }
        for frame in request.frames
    ]
    return UntrustedBlock(tag="frames", attrs={}, content=json.dumps(payload, indent=2))


def _breadcrumb_blocks(request: ExtractionRequest) -> tuple[UntrustedBlock, ...]:
    blocks = []
    for index, breadcrumb in enumerate(request.breadcrumbs):
        attrs = {"index": str(index)}
        timestamp = breadcrumb.get("ts") or breadcrumb.get("timestamp")
        if isinstance(timestamp, str):
            attrs["ts"] = timestamp
        blocks.append(UntrustedBlock(tag="breadcrumb", attrs=attrs, content=json.dumps(breadcrumb)))
    return tuple(blocks)


def _request_block(request: ExtractionRequest) -> UntrustedBlock | None:
    if request.request is None:
        return None
    return UntrustedBlock(tag="request", attrs={}, content=json.dumps(request.request))


def _build_untrusted_blocks(request: ExtractionRequest) -> tuple[UntrustedBlock, ...]:
    blocks = [_exception_block(request), _frames_block(request), *_breadcrumb_blocks(request)]
    request_block = _request_block(request)
    if request_block is not None:
        blocks.append(request_block)
    return tuple(blocks)


class GatewayExtractor:
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

    async def extract(self, request: ExtractionRequest) -> Mapping[str, Any]:
        system_version = self._prompts.get("system")
        task_version = self._prompts.get("understand")
        domain = render_domain_layer(request.language)

        prompt = assemble_prompt(
            system=system_version.text,
            domain=domain,
            task=task_version.text,
            untrusted_blocks=_build_untrusted_blocks(request),
            json_schema=UnderstandExtractionReply.model_json_schema(),
            worked_example=_WORKED_EXAMPLE,
        )

        try:
            result = await self._gateway.complete(
                CompletionRequest(
                    tier="fast",
                    prompt=prompt,
                    output_model=UnderstandExtractionReply,
                    project_id=self._project_id,
                    stage="understand",
                    prompt_version=task_version.prompt_version,
                    investigation_id=self._investigation_id,
                    pipeline_step_id=self._pipeline_step_id,
                    deterministic=True,
                )
            )
        except LLMError as exc:
            raise ExtractorUnavailable(str(exc)) from exc

        return result.output.model_dump(exclude_none=True)
