"""The three-attempt structured-output ladder (`06` §4.1), verbatim:

```
Attempt 1  Native structured output (tool use / response_format=json_schema)
           parse -> Pydantic validate -> success
Attempt 2  On validation failure: REPAIR CALL
           include the original response verbatim
           include the exact validator error
           instruction: "Return ONLY corrected JSON. Change nothing else."
           cheap model tier (this is a formatting task, not a reasoning task)
Attempt 3  Deterministic salvage
           extract the largest balanced {...} block
           strip markdown fences
           repair trailing commas / single quotes
           re-validate
Failure    Stage fails with RT-AI-0003. No partial output is ever accepted.
```

**Every function here is pure — no provider calls.** `gateway.py` owns the
actual repair-call dispatch (it needs the cheap-tier provider list, which
this module has no reason to know about); this module owns everything about
*what makes output valid* and *how to try to fix it deterministically*."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError


@dataclass(frozen=True, slots=True)
class ParseResult[M: BaseModel]:
    output: M | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.output is not None


def parse_and_validate[M: BaseModel](raw_text: str, output_model: type[M]) -> ParseResult[M]:
    """Attempt 1 and attempt 2's re-check, and attempt 3's re-check — the
    same function every time, since "validate" never changes between
    attempts, only what text is being validated does."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        return ParseResult(output=None, error=f"invalid JSON: {exc}")
    try:
        return ParseResult(output=output_model.model_validate(data), error=None)
    except ValidationError as exc:
        return ParseResult(output=None, error=str(exc))


def build_repair_prompt(
    *, template: str, system: str, original_raw_text: str, validator_error: str
) -> tuple[str, str]:
    """Fills `A2` §9's `schema_repair/v1.md` template (`{validator_error}`,
    `{original_response}`) — the literal, binding text, loaded by the
    caller from `ai/prompts/registry.py` rather than duplicated here.
    `system` is likewise supplied by the caller (`ai/prompts`'s shared L1,
    `06` §2.4) rather than a second, drifted copy living in this module.

    T5.1's first draft of this function hardcoded its own repair
    instruction instead of `A2`'s literal text — a real drift, caught and
    fixed at T5.2 once the prompt registry this function should have used
    from the start actually existed."""
    user = template.format(validator_error=validator_error, original_response=original_raw_text)
    return system, user


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _largest_balanced_object(text: str) -> str | None:
    """The largest `{...}` span with balanced braces — a model that wraps
    valid JSON in prose ("Here's the analysis: {...}") is common enough to
    be worth this rather than failing the whole stage over it."""
    best: str | None = None
    start: int | None = None
    depth = 0
    for index, char in enumerate(text):
        if char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    candidate = text[start : index + 1]
                    if best is None or len(candidate) > len(best):
                        best = candidate
    return best


def _repair_single_quotes(candidate: str) -> str:
    """Only applied when the candidate contains **no** double quotes at
    all — the signature of a model that returned a Python `dict` repr
    (`{'key': 'value'}`) rather than JSON. A blind quote-swap on text that
    already has legitimate double-quoted strings would corrupt any value
    containing an apostrophe, so this is deliberately all-or-nothing rather
    than a general single-to-double regex replace."""
    if '"' in candidate:
        return candidate
    return candidate.replace("'", '"')


def salvage(raw_text: str) -> str | None:
    """Attempt 3, deterministic and offline — no model call. Returns `None`
    only if there is nothing brace-shaped to try; a string it returns may
    still fail re-validation, which is a normal outcome the caller handles,
    not a bug in salvage itself."""
    stripped = _FENCE.sub("", raw_text.strip())
    candidate = _largest_balanced_object(stripped) or stripped
    candidate = _TRAILING_COMMA.sub(r"\1", candidate)
    candidate = _repair_single_quotes(candidate)
    return candidate or None
