"""The retrieval plan — the real output of S4 (`03` §S4).

> This stage decides what stage 5 will go and fetch.

Everything here is derived from the payload by rule, with no model involved.
That is what lets Phase 7 be built and measured before Phase 8 exists, and it
is also the permanent floor: when the extractor is unavailable, times out, or
returns JSON that fails validation twice, `03` §S4 requires the stage to
**continue** on the pre-parse rather than terminate. A retrieval plan that only
existed when an LLM was reachable would make that fallback useless.

The single most valuable rule is the one `A2` §3 puts in capitals:

> When a value is unexpectedly None, the defect is usually in whatever
> PRODUCED that value, not in the code that consumed it.

Every frame in a `NoneType` traceback names the consumer. `null-prop-01` is the
proof: its root cause is `clients/tax_client.py`, which appears in no frame, in
no breadcrumb, and nowhere in the message. No plan built from the stack trace
can name that file — which is why the plan asks for the *symbols* around the
null as well as the files, and why S5's call-graph expansion is the strategy
that actually closes the gap.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from roottrace_worker.pipeline.understand.contracts import (
    EntryPoint,
    ExceptionFamily,
    FailurePoint,
    Frame,
    RetrievalPlan,
)
from roottrace_worker.pipeline.understand.taxonomy import cause_class
from roottrace_worker.pipeline.understand.text import symbols_in_message

#: Ordered worst-first. A breadcrumb's level is the only ranking signal that
#: does not depend on reading its text, and reading its text is how a
#: classifier gets fitted to a corpus.
_LEVEL_RANK: dict[str, int] = {"fatal": 3, "error": 3, "warning": 2, "info": 1, "debug": 0}

_IDENTIFIER_IN_SOURCE = re.compile(r"[A-Za-z_]\w*")

#: Values a stringified local takes when it is the null that caused the error.
#: `vars` are captured as `repr()` by the SDK, so these are strings.
_NULL_LITERALS = frozenset({"None", "null", "undefined", "nil"})

MAX_SEMANTIC_QUERIES = 4


@dataclass(frozen=True, slots=True)
class BreadcrumbSignal:
    index: int
    offset_ms: int | None
    text: str


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def select_breadcrumb(
    # `Sequence[Any]`, not `Sequence[Mapping[...]]`: this list comes straight
    # out of `raw_events.payload`, where a breadcrumb can be a string, a null,
    # or anything else that survived ingest validation. Declaring the shape we
    # want would make the guard below dead code to the type checker and a
    # crash in production.
    breadcrumbs: Sequence[Any] | None,
    error_timestamp: Any = None,
) -> BreadcrumbSignal | None:
    """The one breadcrumb most likely to be the trigger.

    Ranked by level, then by closeness to the error. Nothing here reads the
    breadcrumb's *text* — a rule that scored "503" or "flushed" or "concurrent"
    would score well on this corpus and teach us nothing about the next one.
    The extractor reads the text at T5.2; the deterministic pass ranks only on
    what is structural.

    `03` §S4's worked example is the case that matters, and it survives this
    rule: a `warning` outranks the `info` before it, giving the tax-service 503
    at T-141 ms.
    """
    if not breadcrumbs:
        return None

    error_at = _parse_ts(error_timestamp)

    def offset(crumb: Mapping[str, Any]) -> int | None:
        crumb_at = _parse_ts(crumb.get("ts"))
        if error_at is None or crumb_at is None:
            return None
        return round((error_at - crumb_at).total_seconds() * 1000)

    scored: list[tuple[int, int, int, Mapping[str, Any], int | None]] = []
    for index, crumb in enumerate(breadcrumbs):
        if not isinstance(crumb, Mapping):
            continue
        delta = offset(crumb)
        # Breadcrumbs recorded *after* the error are not the trigger.
        if delta is not None and delta < 0:
            continue
        rank = _LEVEL_RANK.get(str(crumb.get("level", "info")).lower(), 1)
        # Sort key: highest level, then smallest gap, then latest in the list.
        scored.append((-rank, delta if delta is not None else 1 << 30, -index, crumb, delta))

    if not scored:
        return None

    scored.sort(key=lambda item: item[:3])
    _, _, negative_index, crumb, delta = scored[0]
    category = str(crumb.get("category") or "").strip()
    message = str(crumb.get("message") or "").strip()
    text = f"{category}: {message}" if category else message
    if delta is not None:
        text = f"{text} — {delta} ms before the error"
    return BreadcrumbSignal(index=-negative_index, offset_ms=delta, text=text)


def _context_symbols(frame: Mapping[str, Any] | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Identifiers on the failing line that are also locals, and which are null.

    Intersecting the source line with the captured locals is what separates the
    two variables in `subtotal = base_price + tax_amount` from the dozen other
    names in scope. `03` §S4's example implicates exactly those two.
    """
    if not frame:
        return (), ()

    variables = frame.get("vars")
    if not isinstance(variables, Mapping):
        return (), ()

    line = str(frame.get("context_line") or "")
    ordered: list[str] = []
    for match in _IDENTIFIER_IN_SOURCE.finditer(line):
        name = match.group(0)
        if name in variables and name not in ordered:
            ordered.append(name)

    nulls = tuple(name for name in ordered if str(variables.get(name)).strip() in _NULL_LITERALS)
    return tuple(ordered), nulls


def entry_point(frames: Sequence[Frame], request: Mapping[str, Any] | None) -> EntryPoint | None:
    """Where this execution began.

    The outermost `in_app` frame, which is the request handler. When there are
    no `in_app` frames at all, `03` §S4 says to fall back to the request's
    route pattern — the handler is unknown, but the route still tells S5 which
    endpoint to look for.
    """
    request = request or {}
    method = request.get("method")
    pattern = request.get("route_pattern") or request.get("url")

    handler = None
    in_app = [frame for frame in frames if frame.in_app]
    if in_app:
        outermost = in_app[-1]
        if outermost.repo_path and outermost.function:
            handler = f"{outermost.repo_path}::{outermost.function}"

    if not any((method, pattern, handler)):
        return None

    return EntryPoint(
        type="http_route" if pattern else "unknown",
        method=str(method) if method else None,
        pattern=str(pattern) if pattern else None,
        handler=handler,
    )


def failure_point(frames: Sequence[Frame]) -> FailurePoint | None:
    """Where the exception was raised — the innermost `in_app` frame."""
    for frame in frames:
        if frame.in_app:
            return FailurePoint(repo_path=frame.repo_path, function=frame.function, line=frame.line)
    return None


def implicated_symbols(
    frames: Sequence[Frame],
    raw_frames: Sequence[Mapping[str, Any]],
    message: str | None,
) -> tuple[str, ...]:
    """Every symbol worth searching for, most relevant first.

    Order is the contract: S5 spends a finite budget walking this list, so the
    failure point's own function comes first and the entry point's comes last.
    """
    ordered: list[str] = []

    def add(symbol: str | None) -> None:
        if symbol and symbol not in ordered:
            ordered.append(symbol)

    in_app = [frame for frame in frames if frame.in_app]
    if in_app:
        add(in_app[0].function)
        innermost_raw = raw_frames[in_app[0].index] if in_app[0].index < len(raw_frames) else None
        locals_on_line, _ = _context_symbols(innermost_raw)
        for name in locals_on_line:
            add(name)

    for symbol in symbols_in_message(message):
        add(symbol)

    for frame in in_app[1:]:
        add(frame.function)

    return tuple(ordered)


def _semantic_queries(
    family: ExceptionFamily,
    failure: FailurePoint | None,
    null_variables: Sequence[str],
) -> tuple[str, ...]:
    """Natural-language queries for strategy C.

    Strategy C (vector search) is deferred in V1 — `03` §S5 says the code path
    exists and returns empty, because the index is never populated. These are
    therefore built but not yet exercised, and they are mechanical on purpose:
    writing genuinely good semantic queries is a language task and belongs to
    the extractor at T5.2, not to a regex.
    """
    queries: list[str] = []
    for name in null_variables[:2]:
        queries.append(f"where {name} is produced or returned")
    if failure and failure.function:
        queries.append(f"{failure.function} implementation and its callers")
    queries.append(cause_class(family).lower())

    deduped: list[str] = []
    for query in queries:
        if query not in deduped:
            deduped.append(query)
    return tuple(deduped[:MAX_SEMANTIC_QUERIES])


def build_plan(
    *,
    frames: Sequence[Frame],
    raw_frames: Sequence[Mapping[str, Any]],
    family: ExceptionFamily,
    message: str | None,
    breadcrumbs: Sequence[Any] | None,
    error_timestamp: Any = None,
    symbols: Sequence[str] = (),
) -> RetrievalPlan:
    """The deterministic retrieval plan."""
    in_app = [frame for frame in frames if frame.in_app]
    failure = failure_point(frames)

    must_fetch: list[str] = []
    for frame in in_app:
        if frame.repo_path and frame.repo_path not in must_fetch:
            must_fetch.append(frame.repo_path)

    innermost_raw = (
        raw_frames[in_app[0].index] if in_app and in_app[0].index < len(raw_frames) else None
    )
    _, null_variables = _context_symbols(innermost_raw)

    signal = select_breadcrumb(breadcrumbs, error_timestamp)

    return RetrievalPlan(
        must_fetch=tuple(must_fetch),
        should_fetch_by_symbol=tuple(symbols),
        semantic_queries=_semantic_queries(family, failure, null_variables),
        # The failing file's history only. `03` §S5's strategy D also correlates
        # the release diff, but which paths that covers is S5's decision from
        # the bundle it actually assembled, not a guess made here.
        want_git_history_for=(failure.repo_path,) if failure and failure.repo_path else (),
        want_tests_for=(failure.function,) if failure and failure.function else (),
        breadcrumb_signal=signal.text if signal else None,
    )
