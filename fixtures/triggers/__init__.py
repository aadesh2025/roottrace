"""Reproduction harness for the 25 fixture bugs.

`A1` §9: *every fixture bug must be real. If you can't trigger it by running
the code, it isn't a fixture — it's a fiction, and a pipeline that passes on
fiction tells you nothing.*

This package is what makes that rule enforceable. Each case has a trigger that
executes the synthetic repository and reproduces its defect, and T3.1's
acceptance ("every one of the 25 bugs is genuinely present in the code") is
checked by running all of them.

It also serves T3.2. `A1` §9 forbids hand-written stack traces, so the error
payloads are captured from `Reproduction.exception.__traceback__` — a real
traceback through real frames at real line numbers, rather than a plausible
transcription of one.

**The harness lives outside `synthetic-repo/`** so the repository stays a
believable service. A checkout API does not ship a directory of scripts that
deliberately break it, and retrieval would learn the wrong thing from one.
"""

from fixtures.triggers.cases import (
    CASE_IDS,
    Reproduction,
    reproduce,
    reproduce_all,
)

__all__ = ["CASE_IDS", "Reproduction", "reproduce", "reproduce_all"]
