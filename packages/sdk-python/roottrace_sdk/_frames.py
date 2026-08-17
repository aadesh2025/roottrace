"""Traceback → `error.stack_frames` (`03` §S1).

The frame list is the seed for retrieval (`03` §S4) and one of the two inputs
to the fingerprint (`02` §S2 uses `basename::function` of the deepest in-app
frames). Getting `in_app` wrong is therefore not cosmetic: a traceback whose
frames are all marked in-app fingerprints on `site-packages/urllib3/…` and
sends retrieval into a dependency the customer's repository does not contain.

Line and column numbers come from `traceback.StackSummary`, which since 3.11
carries `colno` — parsing them out of formatted text would re-derive what the
interpreter already knows and would break on any traceback format change.
"""

from __future__ import annotations

import contextlib
import linecache
import platform
import re
import socket
import sysconfig
import traceback
from pathlib import Path
from typing import Any

from roottrace_sdk._config import DEFAULT_CONTEXT_LINES, DEFAULT_MAX_FRAMES

#: Everything under one of these is library code, not the customer's.
_VENDOR_MARKERS = ("site-packages", "dist-packages", "node_modules")

_STDLIB_PATHS = tuple(
    Path(path).resolve()
    for path in {
        sysconfig.get_paths().get("stdlib"),
        sysconfig.get_paths().get("platstdlib"),
        sysconfig.get_paths().get("purelib"),
        sysconfig.get_paths().get("platlib"),
    }
    if path
)

#: Our own frames are never the customer's code. Without this, an exception
#: raised inside the SDK would be reported as an application bug.
_SDK_ROOT = Path(__file__).resolve().parent

#: Local-variable names whose *value* is redacted when `capture_locals` is on.
#: Deliberately name-based: the value is already a `repr` by the time we see
#: it, and `hunter2` is neither high-entropy nor pattern-shaped, so the server
#: pass would not catch it.
_SECRET_NAME = re.compile(
    r"(?i)(pass(word|wd)?|secret|token|api[_-]?key|authorization|auth|credential|cookie|session|"
    r"private[_-]?key|signature|otp|pin)"
)

MAX_LOCAL_REPR = 200
MAX_LOCALS_PER_FRAME = 25


def is_in_app(filename: str, include: tuple[str, ...] = ()) -> bool:
    """Is this frame the customer's own code?

    `include` forces a prefix in-app, which is how a project whose code *is*
    installed into site-packages (an editable install, a packaged service) gets
    the right answer.
    """
    if not filename or filename.startswith("<"):
        # `<string>`, `<stdin>`, `<frozen importlib._bootstrap>` — synthesised
        # frames with no file behind them.
        return False

    try:
        resolved = Path(filename).resolve()
    except (OSError, ValueError):
        return False

    text = str(resolved)
    for prefix in include:
        if text.startswith(str(Path(prefix).resolve())):
            return True

    if resolved.is_relative_to(_SDK_ROOT):
        return False
    if any(marker in resolved.parts for marker in _VENDOR_MARKERS):
        return False
    return not any(resolved.is_relative_to(stdlib) for stdlib in _STDLIB_PATHS)


def parse_frames(
    exc: BaseException,
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    context_lines: int = DEFAULT_CONTEXT_LINES,
    capture_locals: bool = False,
    in_app_include: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    """The frame list, outermost first, truncated from the *outer* end.

    Truncation keeps the deepest frames because that is where the exception
    was raised and what both the fingerprint and retrieval read. A recursion
    error's traceback is thousands of identical frames; keeping the first fifty
    of those would send a payload containing nothing at all.
    """
    # `TracebackException`, not `StackSummary.extract(walk_tb(...))`. The
    # latter is the obvious spelling and silently loses the column: it feeds
    # the extractor `(frame, lineno)` pairs, so `colno` comes back `None` on
    # every frame. `TracebackException` walks with full positions.
    summary = traceback.TracebackException(
        type(exc), exc, exc.__traceback__, capture_locals=capture_locals
    ).stack
    if max_frames > 0:
        summary = traceback.StackSummary.from_list(summary[-max_frames:])

    return [_frame(item, context_lines, capture_locals, in_app_include) for item in summary]


def _frame(
    item: traceback.FrameSummary,
    context_lines: int,
    capture_locals: bool,
    in_app_include: tuple[str, ...],
) -> dict[str, Any]:
    filename = item.filename or ""
    lineno = item.lineno or 0

    frame: dict[str, Any] = {
        "file": filename,
        "line": lineno,
        "function": item.name,
        "in_app": is_in_app(filename, in_app_include),
    }

    # `colno` is a 0-based byte offset; `line` is 1-based. Reporting both as
    # 1-based keeps a citation like `file:line:column` internally consistent.
    if item.colno is not None:
        frame["column"] = item.colno + 1

    if item.line:
        frame["context_line"] = item.line

    if context_lines > 0 and lineno and filename and not filename.startswith("<"):
        pre, post = _context(filename, lineno, context_lines)
        if pre:
            frame["pre_context"] = pre
        if post:
            frame["post_context"] = post

    if capture_locals and item.locals:
        frame["vars"] = _redact_locals(item.locals)

    return frame


def _context(filename: str, lineno: int, count: int) -> tuple[list[str], list[str]]:
    """Surrounding source, read from `linecache`.

    `linecache.checkcache()` is deliberately not called: it stats every cached
    file on every capture, and this runs in a crashing request path. A stale
    line after a hot reload is a far smaller problem than the syscall storm.
    """
    lines = linecache.getlines(filename)
    if not lines:
        return [], []
    index = lineno - 1
    pre = [line.rstrip("\n") for line in lines[max(0, index - count) : index]]
    post = [line.rstrip("\n") for line in lines[index + 1 : index + 1 + count]]
    return pre, post


def _redact_locals(values: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for name, value in list(values.items())[:MAX_LOCALS_PER_FRAME]:
        if _SECRET_NAME.search(name):
            redacted[name] = "[REDACTED:local_name]"
            continue
        text = str(value)
        redacted[name] = text if len(text) <= MAX_LOCAL_REPR else text[:MAX_LOCAL_REPR] + "…"
    return redacted


def format_exception(exc: BaseException) -> str:
    """`error.stack_trace` — the human-readable form, including any cause.

    Sent alongside `stack_frames` rather than instead of it: the structured
    frames are what retrieval reads, and the text is what a person reads in the
    dashboard and what the fingerprint's message normalisation falls back to
    when an SDK could not pre-parse.
    """
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


def runtime_context(
    framework: str | None = None, framework_version: str | None = None
) -> dict[str, Any]:
    """`03` §S1's `runtime` block."""
    context: dict[str, Any] = {
        "language": "python",
        "language_version": platform.python_version(),
        "os": platform.system().lower(),
    }
    with contextlib.suppress(OSError):  # a host with no resolvable name
        context["hostname"] = socket.gethostname()
    if framework:
        context["framework"] = framework
    if framework_version:
        context["framework_version"] = framework_version
    return context
