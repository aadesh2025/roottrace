"""Frame extraction, `in_app` classification, and path resolution steps 1 and 2.

**Why only steps 1 and 2 of the four-step cascade.** `08` §3.2 defines four steps;
the last two (suffix matching against the repository tree, then filename
search) need the tree, and `03` §8.1 is explicit that *"S4 fetching a file to
'check' a path"* is a boundary violation — **S4 has no repo access by design;
it produces a plan.** `03` §S4's own failure-mode table says where they go:
*"Path mapping produces no plausible repo path → set frame confidence 0.3;
**S5 falls back to filename search across the tree**."* So the cascade spans
two stages, and T4.2 completes it on the S5 side.

That split is visible in the corpus. Twenty-four of the twenty-five payloads
resolve exactly here. `config-02` reports
`/workspace/services/services/export.py`, and stripping the documented
`/workspace/` prefix yields `services/services/export.py`, which is not a file.
Nothing available to this stage can tell that — the path is well-formed and the
prefix is one of the documented ones — so it is returned at heuristic
confidence and S5 corrects it. `A1` §7 put that case in the corpus precisely to
keep step 3 honest, so it is left to fail here rather than papered over with a
project-specific mapping.

**Frame order is innermost-first** throughout, matching `03` §S1 and the SDK.
A CPython traceback prints the opposite way round, so `parse_traceback`
reverses what it reads. Getting this backwards would make every failure point
an entry point and vice versa, and nothing downstream would look obviously
wrong — it would just diagnose the wrong end of the stack.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from roottrace_worker.pipeline.understand.contracts import Frame

#: `08` §3.2 fixes a confidence per cascade step. They are not tuning knobs:
#: S5 ranks on them and the dashboard shows them, so a resolution that was
#: guessed must not be presented as one that was configured.
CONFIDENCE_CONFIGURED = 0.95
CONFIDENCE_HEURISTIC = 0.80
CONFIDENCE_UNRESOLVED = 0.30

#: Below this, `03` §S4 requires `low_frame_confidence` to be flagged.
CONFIDENCE_FLOOR = 0.5

#: `08` §3.2 step 2, as literal prefixes. Longest match wins, so
#: `/usr/src/app/` is stripped rather than leaving `app/` behind.
_HEURISTIC_PREFIXES: tuple[str, ...] = (
    "/app/",
    "/usr/src/app/",
    "/usr/src/",
    "/workspace/",
    "/srv/",
    "/var/task/",
    "/var/www/",
    "/opt/app/",
    "/code/",
)

#: The two entries in `08` §3.2's list that are patterns rather than literals:
#: `/home/*/` and `C:\...\`. The Windows rule stops at a segment named `app`,
#: which is what makes `C:\build\app\services\export.py` resolve; a rule that
#: stripped leading directories until something looked repo-shaped would also
#: eat the `src/` of a repository that genuinely has one.
_HEURISTIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/home/[^/]+/"),
    re.compile(r"^[A-Za-z]:/(?:[^/]+/)*?app/"),
)

#: A frame under any of these is not the customer's code, whatever the payload
#: claims. `03` §S4: *exclude site-packages, node_modules, dist-packages,
#: stdlib paths, vendor/, .venv/*.
_NOT_IN_APP: tuple[str, ...] = (
    "/site-packages/",
    "/dist-packages/",
    "/node_modules/",
    "/vendor/",
    "/.venv/",
    "/venv/",
    "/.tox/",
    "/lib/python",
    "/usr/lib/",
    "/usr/local/lib/",
)

#: CPython's frame line: `  File "x.py", line 12, in fn`.
_TRACEBACK_FRAME = re.compile(
    r'^\s*File "(?P<file>.+?)", line (?P<line>\d+)(?:, in (?P<function>.+?))?\s*$'
)


@dataclass(frozen=True, slots=True)
class PathMapping:
    """One configured mapping, from `repositories.path_mappings` (`04` §7)."""

    source: str
    target: str


@dataclass(frozen=True, slots=True)
class ResolvedPath:
    repo_path: str | None
    confidence: float


def _posix(path: str) -> str:
    return path.replace("\\", "/")


def is_in_app(path: str) -> bool:
    """Whether a path is the customer's own code.

    Conservative by construction: a path this returns `False` for is never
    fetched, and fetching a dependency's source would spend the context budget
    on code the patch may not touch anyway.
    """
    normalised = _posix(path)
    if not normalised or normalised.startswith("<"):
        # `<frozen importlib._bootstrap>`, `<string>`, `<stdin>` — real frames,
        # but there is no file to fetch.
        return False
    lowered = normalised.lower()
    return not any(marker in lowered for marker in _NOT_IN_APP)


def resolve_path(path: str, mappings: Sequence[PathMapping] = ()) -> ResolvedPath:
    """Steps 1 and 2 of `08` §3.2's cascade.

    Returns `repo_path=None` at `CONFIDENCE_UNRESOLVED` rather than returning
    the input unchanged when nothing matches. An unresolved absolute path that
    was passed through would be handed to S5 as though it were repo-relative,
    and S5 would fetch `/app/services/checkout.py` from the repository root.
    """
    normalised = _posix(path)
    if not normalised or normalised.startswith("<"):
        return ResolvedPath(repo_path=None, confidence=CONFIDENCE_UNRESOLVED)

    # Step 1 — configured mappings. Longest source first, so a project that
    # configures both `/app/` and `/app/services/` gets the specific one.
    for mapping in sorted(mappings, key=lambda m: len(m.source), reverse=True):
        source = _posix(mapping.source)
        if source and normalised.startswith(source):
            candidate = _posix(mapping.target) + normalised[len(source) :]
            return ResolvedPath(
                repo_path=posixpath.normpath(candidate).lstrip("/"),
                confidence=CONFIDENCE_CONFIGURED,
            )

    # Step 2 — heuristic prefix stripping.
    for prefix in sorted(_HEURISTIC_PREFIXES, key=len, reverse=True):
        if normalised.startswith(prefix):
            return ResolvedPath(
                repo_path=normalised[len(prefix) :].lstrip("/"),
                confidence=CONFIDENCE_HEURISTIC,
            )
    for pattern in _HEURISTIC_PATTERNS:
        if (match := pattern.match(normalised)) is not None:
            return ResolvedPath(
                repo_path=normalised[match.end() :].lstrip("/"),
                confidence=CONFIDENCE_HEURISTIC,
            )

    # Already relative — a runtime that reports paths relative to the working
    # directory needs no mapping at all.
    if not normalised.startswith("/") and not re.match(r"^[A-Za-z]:/", normalised):
        return ResolvedPath(repo_path=normalised.lstrip("./"), confidence=CONFIDENCE_HEURISTIC)

    return ResolvedPath(repo_path=None, confidence=CONFIDENCE_UNRESOLVED)


def parse_traceback(text: str | None) -> tuple[dict[str, Any], ...]:
    """Frames from a CPython traceback, innermost first.

    Used only when the payload carries no `stack_frames` — an SDK sends both,
    but a curl'd payload or a third-party forwarder may send only the text.
    """
    if not text:
        return ()

    frames: list[dict[str, Any]] = []
    for line in text.splitlines():
        if (match := _TRACEBACK_FRAME.match(line)) is None:
            continue
        frames.append(
            {
                "file": match.group("file"),
                "line": int(match.group("line")),
                "function": match.group("function"),
            }
        )
    # Printed outermost-first; `03` §S1 stores innermost-first.
    frames.reverse()
    return tuple(frames)


def extract_frames(
    error: Mapping[str, Any],
    *,
    mappings: Sequence[PathMapping] = (),
) -> tuple[Frame, ...]:
    """The pre-parse's frame list: extracted, classified, and resolved.

    `in_app` is **computed**, and the payload may only demote. A client knows
    its own project root and may legitimately mark a frame out-of-app that
    looks in-app to us; the reverse is not true, and a payload that marked
    `/usr/lib/python3.12/json/decoder.py` as in-app would put the standard
    library into a patch's blast radius.
    """
    raw_frames: Sequence[Mapping[str, Any]] | tuple[dict[str, Any], ...]
    reported = error.get("stack_frames")
    if isinstance(reported, list) and reported:
        raw_frames = [frame for frame in reported if isinstance(frame, Mapping)]
    else:
        raw_frames = parse_traceback(
            error.get("stack_trace") if isinstance(error.get("stack_trace"), str) else None
        )

    frames: list[Frame] = []
    for index, raw in enumerate(raw_frames):
        raw_path = str(raw.get("file") or "")
        computed_in_app = is_in_app(raw_path)
        claimed = raw.get("in_app")
        in_app = computed_in_app and claimed is not False

        resolved = resolve_path(raw_path, mappings) if in_app else ResolvedPath(None, 0.0)

        line = raw.get("line")
        function = raw.get("function")
        frames.append(
            Frame(
                index=index,
                raw_path=raw_path,
                repo_path=resolved.repo_path,
                line=line if isinstance(line, int) and line >= 1 else None,
                function=str(function) if function else None,
                in_app=in_app,
                confidence=resolved.confidence,
            )
        )
    return tuple(frames)
