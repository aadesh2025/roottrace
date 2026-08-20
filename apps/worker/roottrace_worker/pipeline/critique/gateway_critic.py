"""The real `StructuredCritic` (`03` §S10, T7.2) — calls `LLMGateway`
with `critique/v2.md` on the `reasoning-b` tier (`06` §8.1: "S10 critique
| reasoning-b | 20k / 1.2k | $0.070").

**`critique/v2.md` replaces L1 entirely rather than extending it** — `A2`
§6's own heading, "System layer override" — unlike every other stage's
prompt file, which is only ever a task layer appended to the shared
`system/v1.md`. `_split_prompt` is the first real reader of the file's
own leading comment ("Whoever wires S10 (T7.2) reads this file in two
parts, split on the `---` below"), written ahead of this ticket during
T5.1/T5.2's prompt-registry work, same precedent as `repair/v1.md` and
`gate_instructions.py` for T7.1. The comment itself is stripped before
either half reaches a model — it is documentation for this file's future
implementer, not part of the prompt."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from roottrace_worker.ai.contracts import CompletionRequest
from roottrace_worker.ai.errors import LLMError
from roottrace_worker.ai.gateway import LLMGateway
from roottrace_worker.ai.prompts.assembly import UntrustedBlock, assemble_prompt
from roottrace_worker.ai.prompts.registry import PromptRegistry
from roottrace_worker.pipeline.critique.critic import CriticUnavailable, CritiqueRequest
from roottrace_worker.pipeline.critique.extraction_schema import CritiqueReply
from roottrace_worker.pipeline.retrieve.bundle import BundleFile, ContextBundle

#: `03` §S10's own worked example, trimmed to what `CritiqueReply` and
#: `stage.py` together actually keep — `blocking` is included because the
#: model is asked to produce it (`A2` §6), even though `stage.py` never
#: trusts it (`contracts.py`'s module docstring).
_WORKED_EXAMPLE = json.dumps(
    {
        "verdict": "approve_with_notes",
        "agreement_with_diagnosis": 0.9,
        "addresses_reported_error": True,
        "findings": [
            {
                "severity": "medium",
                "dimension": "completeness",
                "statement": "services/quote.py::estimate_total calls the same get_rate "
                "and will now propagate TaxServiceUnavailable to an unprepared caller.",
                "evidence": {"repo_path": "services/quote.py", "line_range": [31, 36]},
                "recommendation": "Acceptable to leave out of scope, but the PR "
                "description must state it explicitly so a reviewer isn't surprised.",
            },
            {
                "severity": "low",
                "dimension": "style",
                "statement": "The new exception class is defined inline in tax_client.py "
                "while sibling exceptions live in errors.py.",
                "recommendation": "Move TaxServiceUnavailable to clients/errors.py for "
                "consistency.",
            },
        ],
        "security_review": {"concerns": [], "clean": True},
        "regression_risk": "low",
        "test_quality": {
            "reproduces_bug": True,
            "assessment": "The test mocks a 503 and asserts the typed exception; it is a "
            "genuine reproduction, not a tautology.",
        },
        "scope_assessment": "Tightly scoped. No unrelated changes.",
        "blocking": False,
    },
    indent=2,
)

#: `A2` §6's own leading HTML comment — documentation for this file's
#: implementer, stripped before either half of `critique/v2.md` reaches a
#: model. `re.DOTALL` so the comment's own newlines match `.`.
_LEADING_COMMENT = re.compile(r"\A<!--.*?-->\s*", re.DOTALL)
#: The delimiter `critique/v2.md`'s own comment names: a line containing
#: exactly `---`, separating the system-layer override from the task layer.
_SECTION_BREAK = re.compile(r"^---$", re.MULTILINE)


def split_critique_prompt(text: str) -> tuple[str, str]:
    """`(system_override, task)` — the two halves `A2` §6 and `critique/
    v2.md`'s own comment describe. Raises `ValueError` rather than
    silently treating the whole file as one layer if the expected `---`
    break is ever missing — a malformed prompt file should fail loudly at
    load time, not send an empty task layer to a live model."""
    without_comment = _LEADING_COMMENT.sub("", text, count=1)
    parts = _SECTION_BREAK.split(without_comment, maxsplit=1)
    if len(parts) != 2:
        raise ValueError("critique/v2.md is missing its system/task '---' section break")
    system_override, task = parts
    return system_override.strip(), task.strip()


def _error_block(request: CritiqueRequest) -> UntrustedBlock:
    exc = request.understanding.exception
    payload = {
        "type": exc.type,
        "message": exc.message_normalized,
        "frames": [
            {"repo_path": f.repo_path, "line": f.line, "function": f.function, "in_app": f.in_app}
            for f in request.understanding.frames
        ],
    }
    return UntrustedBlock(tag="original_error", attrs={}, content=json.dumps(payload, indent=2))


def _file_block(file: BundleFile) -> UntrustedBlock:
    attrs = {"path": file.repo_path, "lines": f"{file.line_range[0]}-{file.line_range[1]}"}
    if file.blame is not None:
        attrs["sha"] = file.blame.commit.sha
    return UntrustedBlock(tag="file", attrs=attrs, content=file.content)


def _bundle_blocks(bundle: ContextBundle) -> tuple[UntrustedBlock, ...]:
    return tuple(_file_block(file) for file in bundle.files)


def _diff_block(request: CritiqueRequest) -> UntrustedBlock:
    return UntrustedBlock(tag="final_diff", attrs={}, content=request.diff)


def _sandbox_results_block(request: CritiqueRequest) -> UntrustedBlock:
    return UntrustedBlock(
        tag="sandbox_results",
        attrs={"passed": str(request.validation.passed), "mode": request.validation.mode},
        content=request.validation.model_dump_json(indent=2),
    )


def _build_untrusted_blocks(request: CritiqueRequest) -> tuple[UntrustedBlock, ...]:
    return (
        _error_block(request),
        *_bundle_blocks(request.bundle),
        _diff_block(request),
        _sandbox_results_block(request),
    )


class GatewayCritic:
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

    async def critique(self, request: CritiqueRequest) -> Mapping[str, Any]:
        task_version = self._prompts.get("critique")
        system_override, task_text = split_critique_prompt(task_version.text)

        prompt = assemble_prompt(
            system=system_override,
            domain=None,
            task=task_text,
            untrusted_blocks=_build_untrusted_blocks(request),
            json_schema=CritiqueReply.model_json_schema(),
            worked_example=_WORKED_EXAMPLE,
        )

        try:
            result = await self._gateway.complete(
                CompletionRequest(
                    tier="reasoning-b",
                    prompt=prompt,
                    output_model=CritiqueReply,
                    project_id=self._project_id,
                    stage="critique",
                    prompt_version=task_version.prompt_version,
                    investigation_id=self._investigation_id,
                    pipeline_step_id=self._pipeline_step_id,
                )
            )
        except LLMError as exc:
            raise CriticUnavailable(str(exc)) from exc

        payload = result.output.model_dump()
        payload["model"] = result.model
        payload["prompt_version"] = result.prompt_version
        payload["tokens"] = {"prompt": result.tokens_in, "completion": result.tokens_out}
        return payload
