"""S4 — `understand` (`03` §S4).

> Convert an unstructured error into a precise, machine-usable structure — and,
> crucially, into a **retrieval plan**. This stage decides what stage 5 will go
> and fetch.

Three steps, in order: deterministic pre-parse, structured extraction,
deterministic post-validation. Only the middle one needs a model, and it is the
only one allowed to fail — `03` §S4 makes S4 **never terminal**, so an
unreachable provider costs precision, never the investigation.

The stage is a function of its arguments. It writes no `pipeline_steps` row and
publishes no WebSocket frame: those are universal invariants (`03` §8.2) owned
by the orchestrator (T8.2), and a stage that wrote its own row would write a
second one the moment the orchestrator resumed it (R3).

**Budget** (`03` §6): target p95 3 s, hard timeout 10 s, 2,000 output tokens,
2 retries. The timeout is the caller's to impose — a stage that raced its own
clock would report success on a partial result.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from roottrace_worker.pipeline.understand.contracts import (
    ErrorUnderstanding,
    ExceptionInfo,
    Flag,
)
from roottrace_worker.pipeline.understand.extractor import (
    ExtractionRequest,
    ExtractorUnavailable,
    StructuredExtractor,
)
from roottrace_worker.pipeline.understand.frames import (
    CONFIDENCE_FLOOR,
    PathMapping,
    extract_frames,
    parse_traceback,
)
from roottrace_worker.pipeline.understand.plan import (
    build_plan,
    entry_point,
    failure_point,
    implicated_symbols,
)
from roottrace_worker.pipeline.understand.taxonomy import classify, retrieval_hint
from roottrace_worker.pipeline.understand.text import (
    looks_like_an_instruction,
    normalize_message,
)
from roottrace_worker.pipeline.understand.validate import apply_extraction

#: `03` §S4: the deterministic fallback reports exactly this. It is not a
#: tuning value — S11's confidence engine reads it, and a fallback that
#: reported 0.9 would let a stack-trace-only understanding score as though a
#: model had corroborated it.
DETERMINISTIC_CONFIDENCE = 0.5

#: Routes that exist for infrastructure rather than for a user. An error on one
#: of these is real and worth investigating, but nobody's checkout failed, and
#: severity and PR framing downstream read this field.
_INTERNAL_ROUTE = re.compile(
    r"^/(?:health|healthz|readyz|livez|ready|live|ping|metrics|status|_internal)\b"
)

_PYTHON_TRACEBACK = re.compile(r"^Traceback \(most recent call last\)", re.MULTILINE)
_JS_STACK = re.compile(r"^\s*at\s+\S+\s*\(", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class UnderstandOutcome:
    """The stage's return value.

    `understanding` is the contract (`03` §S4). The other two are observability
    (R5): the orchestrator records them on the `pipeline_steps` row, and
    `dropped_claims` in particular is how a degrading extractor becomes visible
    instead of merely making the plans quietly worse.
    """

    understanding: ErrorUnderstanding
    dropped_claims: tuple[str, ...] = ()
    extraction_performed: bool = False


def detect_language(runtime: Mapping[str, Any], stack_trace: str | None) -> str | None:
    """From `runtime.language`, falling back to the shape of the trace."""
    reported = runtime.get("language")
    if isinstance(reported, str) and reported.strip():
        return reported.strip().lower()
    if stack_trace:
        if _PYTHON_TRACEBACK.search(stack_trace):
            return "python"
        if _JS_STACK.search(stack_trace):
            return "javascript"
    return None


def is_user_facing(request: Mapping[str, Any] | None) -> bool:
    """Whether a person was waiting on this request when it failed."""
    if not request:
        return False
    route = request.get("route_pattern") or request.get("url")
    if not isinstance(route, str) or not route:
        return False
    return not _INTERNAL_ROUTE.match(route)


def _mapping(value: Any) -> Mapping[str, Any]:
    """A mapping or an empty one. Narrows for the type checker without an
    `assert`, which `-O` would strip and which would turn a merely odd payload
    into a crash in the one stage that must never fail."""
    return value if isinstance(value, Mapping) else {}


def _raw_frames(error: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    reported = error.get("stack_frames")
    if isinstance(reported, list) and reported:
        return tuple(frame for frame in reported if isinstance(frame, Mapping))
    trace = error.get("stack_trace")
    return parse_traceback(trace if isinstance(trace, str) else None)


def _suspicious(event: Mapping[str, Any], error: Mapping[str, Any]) -> bool:
    """`A2` §2 rule 5 — instruction-shaped text inside untrusted data.

    Detected at the boundary and recorded, never obeyed. The fencing that makes
    it safe to send is T5.2's; noticing it is free and belongs wherever the
    payload is first read carefully.
    """
    candidates: list[Any] = [error.get("message"), error.get("stack_trace")]
    breadcrumbs = event.get("breadcrumbs")
    if isinstance(breadcrumbs, list):
        candidates.extend(
            crumb.get("message") for crumb in breadcrumbs if isinstance(crumb, Mapping)
        )
    return any(looks_like_an_instruction(text) for text in candidates if isinstance(text, str))


def preparse(
    event: Mapping[str, Any],
    *,
    mappings: Sequence[PathMapping] = (),
) -> ErrorUnderstanding:
    """Step 1 — everything knowable without a model.

    This is also the whole output when the extractor is unavailable, so it is
    written to stand alone rather than to be a scaffold something else fills in.
    """
    error = _mapping(event.get("error"))
    runtime = _mapping(event.get("runtime"))
    request = event.get("request") if isinstance(event.get("request"), Mapping) else None
    raw_breadcrumbs = event.get("breadcrumbs")
    breadcrumbs = raw_breadcrumbs if isinstance(raw_breadcrumbs, list) else []

    message = error.get("message") if isinstance(error.get("message"), str) else ""
    stack_trace = error.get("stack_trace") if isinstance(error.get("stack_trace"), str) else None
    exception_type = str(error.get("type") or "")

    frames = extract_frames(error, mappings=mappings)
    raw_frames = _raw_frames(error)
    family = classify(exception_type, message)
    framework = runtime.get("framework")

    symbols = implicated_symbols(frames, raw_frames, message)
    plan = build_plan(
        frames=frames,
        raw_frames=raw_frames,
        family=family,
        message=message,
        breadcrumbs=breadcrumbs,
        error_timestamp=event.get("timestamp"),
        symbols=symbols,
    )

    in_app = [frame for frame in frames if frame.in_app]
    flags: list[Flag] = [Flag.DETERMINISTIC_ONLY]
    if not frames and not stack_trace:
        flags.append(Flag.NO_STACK_TRACE)
    if not in_app:
        flags.append(Flag.NO_IN_APP_FRAMES)
    if not any(frame.confidence >= CONFIDENCE_FLOOR for frame in in_app):
        flags.append(Flag.LOW_FRAME_CONFIDENCE)
    if _suspicious(event, error):
        flags.append(Flag.SUSPICIOUS_CONTENT_DETECTED)

    return ErrorUnderstanding(
        language=detect_language(runtime, stack_trace),
        framework=str(framework) if isinstance(framework, str) and framework else None,
        exception=ExceptionInfo(
            type=exception_type,
            family=family,
            message_normalized=normalize_message(message),
            is_user_facing=is_user_facing(request),
        ),
        frames=frames,
        entry_point=entry_point(frames, request),
        failure_point=failure_point(frames),
        implicated_symbols=symbols,
        # Empty, and not for want of trying. A hypothesis is a claim about why
        # the code failed, and nothing available to a regex can make one
        # honestly — a fabricated prior would flow into S6's elimination and
        # then into the confidence score as though it had been reasoned.
        initial_hypotheses=(),
        retrieval_plan=plan,
        notes=(
            "Deterministic pre-parse only — no structured extraction was performed. "
            f"Taxonomy hint for {family.value}: {retrieval_hint(family)}."
        ),
        extraction_confidence=DETERMINISTIC_CONFIDENCE,
        flags=tuple(flags),
    )


async def understand(
    event: Mapping[str, Any],
    *,
    mappings: Sequence[PathMapping] = (),
    extractor: StructuredExtractor | None = None,
) -> UnderstandOutcome:
    """Run S4 over one error event.

    `event` is a sanitised `raw_events.payload` (S1 has already redacted it).
    `mappings` come from `repositories.path_mappings` (`04` §7).
    """
    base = preparse(event, mappings=mappings)

    if extractor is None:
        return UnderstandOutcome(understanding=base)

    error = _mapping(event.get("error"))
    breadcrumbs = event.get("breadcrumbs")
    request = event.get("request") if isinstance(event.get("request"), Mapping) else None

    # Typed `Any` deliberately. The Protocol promises a `Mapping`, and the
    # check below would be dead code if that promise were trusted — but the
    # implementation behind this seam will be a model reply parsed by a
    # gateway (T5.1), and S4 must never crash on one. The type says what is
    # contracted; the check handles what arrives.
    reply: Any
    try:
        reply = await extractor.extract(
            ExtractionRequest(
                language=base.language,
                framework=base.framework,
                exception_type=base.exception.type,
                exception_message=str(error.get("message") or ""),
                family=base.exception.family,
                frames=base.frames,
                breadcrumbs=tuple(
                    crumb for crumb in (breadcrumbs or []) if isinstance(crumb, Mapping)
                ),
                request=request,
            )
        )
    except ExtractorUnavailable:
        # `03` §S4: never terminal. The pre-parse is a complete, usable answer.
        return UnderstandOutcome(understanding=base)

    if not isinstance(reply, Mapping):
        return UnderstandOutcome(understanding=base)

    merged, dropped = apply_extraction(base, reply)
    return UnderstandOutcome(
        understanding=merged, dropped_claims=dropped, extraction_performed=True
    )
