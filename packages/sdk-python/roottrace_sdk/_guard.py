"""The never-raises guarantee (`05` §10).

> **never raises into the host application** — an observability SDK that can
> crash the app it observes is worse than no SDK.

Two things follow from that sentence, and the second is the one that is easy to
get wrong.

**It catches `Exception`, not `BaseException`.** `KeyboardInterrupt`,
`SystemExit` and `asyncio.CancelledError` are control flow, not failure.
Swallowing a `CancelledError` inside an ASGI middleware turns a cancelled
request into a hung one, which is a worse outcome than the crash the guarantee
exists to prevent.

**A guard that swallows silently is indistinguishable from a guard that never
fires.** So every swallowed exception is *reported* — to registered sinks, and
to stderr when the SDK is in debug mode. That is what makes the guarantee
testable: a test installs a sink, breaks an internal seam, and asserts both
that the public call returned normally *and* that the guard is what caught it.
Without the sink, "it did not raise" is satisfied equally well by code that
never ran.
"""

from __future__ import annotations

import contextlib
import functools
import sys
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")

Sink = Callable[[str, BaseException], None]

_sinks: list[Sink] = []
_debug = False


def set_debug(enabled: bool) -> None:
    global _debug
    _debug = enabled


def add_sink(sink: Sink) -> Callable[[], None]:
    """Register an observer of swallowed exceptions. Returns its remover."""
    _sinks.append(sink)

    def remove() -> None:
        if sink in _sinks:
            _sinks.remove(sink)

    return remove


def report(where: str, exc: BaseException) -> None:
    """Announce a swallowed exception without ever raising a new one.

    Sinks are hostile by the same standard as everything else: a test sink that
    asserts, or a customer sink with a bug in it, must not become the crash
    this module exists to prevent.
    """
    if _debug:
        with contextlib.suppress(Exception):  # stderr can be closed or replaced
            print(f"[roottrace] suppressed in {where}:", file=sys.stderr)
            traceback.print_exception(exc, file=sys.stderr)
    for sink in list(_sinks):
        with contextlib.suppress(Exception):  # see above
            sink(where, exc)


def warn(message: str) -> None:
    """A configuration problem the developer must see.

    Written to stderr regardless of debug mode. An invalid `api_key` that
    silently disabled reporting would look exactly like an application with no
    errors, which is the failure mode with the longest time-to-discovery.
    Writing one line to stderr is not "raising into the host application".
    """
    with contextlib.suppress(Exception):
        print(f"[roottrace] {message}", file=sys.stderr)


def never_raises(where: str, default: Any = None) -> Callable[[Callable[P, T]], Callable[P, T]]:
    """Wrap a public entry point so no failure of ours reaches the caller.

    `default` is `Any` rather than `T` so the return type is inferred from the
    decorated function alone. Typing it as `T` makes `default=None` bind `T` to
    `None`, which then rejects every function returning `str | None` — the
    signature of most of the public surface.
    """

    def decorate(fn: Callable[P, T]) -> Callable[P, T]:
        @functools.wraps(fn)
        def guarded(*args: P.args, **kwargs: P.kwargs) -> T:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:  # this IS the guarantee
                report(where, exc)
                return default  # type: ignore[no-any-return]

        return guarded

    return decorate


@contextmanager
def suppressed(where: str) -> Iterator[None]:
    """Statement-level form, for the seams inside a background thread."""
    try:
        yield
    except Exception as exc:  # this IS the guarantee
        report(where, exc)
