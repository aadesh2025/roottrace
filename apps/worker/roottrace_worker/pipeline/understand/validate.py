"""Post-validation of the extractor's reply (`03` §S4 step 3).

```
3. Deterministic post-validation:
   ├─ every suspected_file must have plausibly-repo-relative shape
   ├─ every claim must reference a frame index that exists
   └─ on violation → repair prompt (1 retry) → on second violation, drop the claim
```

The repair retry belongs to the gateway's three-attempt ladder (`06` §4.1,
T5.1). What lives here is the last clause: **drop the claim.** Not reject the
response, not fail the stage — remove the unsupportable part and keep the rest.

The merge rule is narrower than "take the model's answer", and deliberately so.
The model may:

- **add** files, symbols and queries to the plan, once each has passed the
  shape check;
- **lower** a frame's path confidence, since `A2` §3 asks it to mark mappings
  that look wrong;
- **replace** the exception family, which is the classification `A2` §3 step 1
  asks it to make and the one place it sees breadcrumbs that the deterministic
  taxonomy refuses to read;
- **supply** hypotheses, notes and its own confidence.

It may not remove a file the frames prove was executing, raise a confidence the
cascade did not earn, invent a frame, or contradict the runtime metadata the
SDK reported as fact. The result is that a hallucinating extractor degrades the
plan toward the deterministic one and can never degrade it below it — which is
the property that makes `03` §S4's "never terminal" fallback safe to rely on.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from roottrace_worker.pipeline.understand.contracts import (
    ErrorUnderstanding,
    ExceptionFamily,
    ExceptionInfo,
    Flag,
    Frame,
    Hypothesis,
    RetrievalPlan,
)

MAX_NOTES_CHARS = 2_000
MAX_HYPOTHESES = 4
MAX_PLAN_ITEMS = 32
MAX_PRIOR_TOTAL = 1.0

#: A repo-relative path: no anchor, no traversal, no drive, has a file
#: extension, and is not inside a dependency directory. The vendor check is
#: repeated from `frames.is_in_app` on purpose — this one guards a path the
#: *model* produced, which never passed through frame classification at all.
_PLAUSIBLE_PATH = re.compile(r"^(?!/)(?![A-Za-z]:)[\w./+-]+\.[A-Za-z0-9]+$")
_VENDOR_SEGMENT = re.compile(
    r"(^|/)(site-packages|dist-packages|node_modules|vendor|\.venv|venv|\.tox)(/|$)"
)
_IDENTIFIER = re.compile(r"^[A-Za-z_][\w.]*$")

#: `frame 3`, `frames[3]`, `frame_index: 3` — the forms a model uses to point
#: at a frame. Any index it names must exist.
_FRAME_REFERENCE = re.compile(r"\bframes?[\s_]*(?:index)?[\s:\[]*(\d+)\]?", re.IGNORECASE)


def is_plausible_repo_path(path: Any) -> bool:
    """Whether a string could be a path in the repository.

    Shape only — S4 has no repo access and cannot check existence (`03` §8.1).
    H3 (path existence) is enforced later against retrieved content; this is
    what stops an absolute path, a traversal, or `/etc/passwd` reaching S5's
    fetch loop in the first place.
    """
    if not isinstance(path, str) or not path or len(path) > 512:
        return False
    if ".." in path or "\x00" in path or "\\" in path:
        return False
    if _VENDOR_SEGMENT.search(path):
        return False
    return bool(_PLAUSIBLE_PATH.match(path))


def references_only_real_frames(text: Any, frame_count: int) -> bool:
    """Whether every frame index a claim names actually exists."""
    if not isinstance(text, str):
        return False
    return all(int(index) < frame_count for index in _FRAME_REFERENCE.findall(text))


def _strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return ()
    return tuple(item for item in value if isinstance(item, str) and item)


def _merge(base: Sequence[str], extra: Sequence[str], keep: Any) -> tuple[str, ...]:
    merged = list(base)
    for item in extra:
        if keep(item) and item not in merged:
            merged.append(item)
    return tuple(merged[:MAX_PLAN_ITEMS])


def _merge_plan(base: RetrievalPlan, reply: Mapping[str, Any]) -> tuple[RetrievalPlan, list[str]]:
    dropped: list[str] = []
    proposed = reply.get("retrieval_plan")
    if not isinstance(proposed, Mapping):
        return base, dropped

    def paths(key: str) -> tuple[str, ...]:
        candidates = _strings(proposed.get(key))
        for candidate in candidates:
            if not is_plausible_repo_path(candidate):
                dropped.append(f"retrieval_plan.{key}: {candidate!r}")
        return candidates

    def symbols(key: str) -> tuple[str, ...]:
        candidates = _strings(proposed.get(key))
        for candidate in candidates:
            if not _IDENTIFIER.match(candidate):
                dropped.append(f"retrieval_plan.{key}: {candidate!r}")
        return candidates

    signal = proposed.get("breadcrumb_signal")

    return (
        RetrievalPlan(
            must_fetch=_merge(base.must_fetch, paths("must_fetch"), is_plausible_repo_path),
            should_fetch_by_symbol=_merge(
                base.should_fetch_by_symbol,
                symbols("should_fetch_by_symbol"),
                lambda item: bool(_IDENTIFIER.match(item)),
            ),
            semantic_queries=_merge(
                base.semantic_queries,
                _strings(proposed.get("semantic_queries")),
                lambda item: len(item) <= 200,
            ),
            want_git_history_for=_merge(
                base.want_git_history_for, paths("want_git_history_for"), is_plausible_repo_path
            ),
            want_tests_for=_merge(
                base.want_tests_for,
                symbols("want_tests_for"),
                lambda item: bool(_IDENTIFIER.match(item)),
            ),
            # The deterministic signal is a quotation of a real breadcrumb. The
            # model's is prose about one, so it only fills a gap.
            breadcrumb_signal=base.breadcrumb_signal
            or (signal.strip()[:500] if isinstance(signal, str) and signal.strip() else None),
        ),
        dropped,
    )


def _hypotheses(
    reply: Mapping[str, Any], frame_count: int
) -> tuple[tuple[Hypothesis, ...], list[str]]:
    dropped: list[str] = []
    proposed = reply.get("initial_hypotheses")
    if not isinstance(proposed, Sequence) or isinstance(proposed, str | bytes):
        return (), dropped

    kept: list[Hypothesis] = []
    running_total = 0.0
    for item in proposed:
        if not isinstance(item, Mapping):
            dropped.append(f"initial_hypotheses: {item!r}")
            continue
        statement = item.get("statement")
        if not isinstance(statement, str) or not statement.strip():
            dropped.append(f"initial_hypotheses: {item!r}")
            continue
        if not references_only_real_frames(statement, frame_count):
            dropped.append(f"initial_hypotheses (nonexistent frame): {statement!r}")
            continue

        try:
            prior = float(item.get("prior", 0.0))
        except (TypeError, ValueError):
            dropped.append(f"initial_hypotheses (unreadable prior): {statement!r}")
            continue
        if not 0.0 <= prior <= 1.0:
            dropped.append(f"initial_hypotheses (prior out of range): {statement!r}")
            continue
        # `A2` §3: priors sum to at most 1.0. Truncating the tail rather than
        # renormalising keeps the surviving priors meaning what the model said
        # they meant — rescaling would silently promote a 0.05 afterthought.
        if running_total + prior > MAX_PRIOR_TOTAL + 1e-9:
            dropped.append(f"initial_hypotheses (priors exceed 1.0): {statement!r}")
            continue

        evidence = tuple(
            need
            for need in _strings(item.get("evidence_needed"))
            if references_only_real_frames(need, frame_count)
        )
        kept.append(Hypothesis(statement=statement.strip(), prior=prior, evidence_needed=evidence))
        running_total += prior
        if len(kept) == MAX_HYPOTHESES:
            break

    return tuple(kept), dropped


def _frames(base: Sequence[Frame], reply: Mapping[str, Any]) -> tuple[tuple[Frame, ...], list[str]]:
    """Apply the model's confidence assessment. Downwards only."""
    dropped: list[str] = []
    proposed = reply.get("frames")
    if not isinstance(proposed, Sequence) or isinstance(proposed, str | bytes):
        return tuple(base), dropped

    lowered: dict[int, float] = {}
    for item in proposed:
        if not isinstance(item, Mapping):
            continue
        index = item.get("index")
        confidence = item.get("confidence")
        if not isinstance(index, int) or not isinstance(confidence, int | float):
            continue
        if not 0 <= index < len(base):
            dropped.append(f"frames[{index}]: no such frame")
            continue
        value = float(confidence)
        if not 0.0 <= value <= 1.0:
            dropped.append(f"frames[{index}]: confidence {confidence!r} out of range")
            continue
        if value < base[index].confidence:
            lowered[index] = value
        elif value > base[index].confidence:
            dropped.append(f"frames[{index}]: refused to raise confidence to {value}")

    if not lowered:
        return tuple(base), dropped

    return (
        tuple(
            frame.model_copy(update={"confidence": lowered[frame.index]})
            if frame.index in lowered
            else frame
            for frame in base
        ),
        dropped,
    )


def apply_extraction(
    base: ErrorUnderstanding, reply: Mapping[str, Any]
) -> tuple[ErrorUnderstanding, tuple[str, ...]]:
    """Merge a validated extractor reply onto the deterministic pre-parse.

    Returns the understanding and every claim that was dropped, so the
    orchestrator can record them on the `pipeline_steps` row. A drop that is
    never counted is a silent quality regression — the stage keeps working and
    nobody learns the extractor got worse.
    """
    dropped: list[str] = []

    frames, frame_drops = _frames(base.frames, reply)
    dropped.extend(frame_drops)

    plan, plan_drops = _merge_plan(base.retrieval_plan, reply)
    dropped.extend(plan_drops)

    hypotheses, hypothesis_drops = _hypotheses(reply, len(base.frames))
    dropped.extend(hypothesis_drops)

    exception = base.exception
    proposed_exception = reply.get("exception")
    if isinstance(proposed_exception, Mapping):
        family = proposed_exception.get("family")
        if isinstance(family, str) and family in set(ExceptionFamily):
            exception = ExceptionInfo(
                type=base.exception.type,
                family=ExceptionFamily(family),
                message_normalized=base.exception.message_normalized,
                is_user_facing=base.exception.is_user_facing,
            )
        elif family is not None:
            dropped.append(f"exception.family: {family!r}")

    symbols = _merge(
        base.implicated_symbols,
        _strings(reply.get("implicated_symbols")),
        lambda item: bool(_IDENTIFIER.match(item)),
    )

    notes = reply.get("notes")
    confidence = reply.get("extraction_confidence")

    flags = tuple(flag for flag in base.flags if flag is not Flag.DETERMINISTIC_ONLY)

    return (
        base.model_copy(
            update={
                "exception": exception,
                "frames": frames,
                "implicated_symbols": symbols,
                "initial_hypotheses": hypotheses,
                "retrieval_plan": plan,
                "notes": notes.strip()[:MAX_NOTES_CHARS] if isinstance(notes, str) else base.notes,
                "extraction_confidence": (
                    min(1.0, max(0.0, float(confidence)))
                    if isinstance(confidence, int | float)
                    else base.extraction_confidence
                ),
                "flags": flags,
            }
        ),
        tuple(dropped),
    )
