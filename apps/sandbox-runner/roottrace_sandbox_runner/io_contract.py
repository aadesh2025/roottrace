"""The I/O contract this process reads and writes (`docs/07` §7), read and
written with the standard library only — see the package `pyproject.toml`
for why: everything here runs inside a container with no network at run
time and must resolve entirely from the offline wheel cache, so this
package stays dependency-free rather than asking the sandbox image to also
cache `pydantic` just to validate its own two JSON files.

**Input arrives over stdin, not `docker cp` to `/opt/roottrace/input.json`
— a correction to `07` §7's B10 note, verified empirically against Docker
Engine 29.5.3, not assumed.** B10's own reasoning (`/work` is a tmpfs
mounted at container *start*, so a `docker cp` write issued at *create*
time — before start — is invisible once that mount lands) is correct, and
staging the input somewhere other than `/work` is still the right idea.
What B10 does not account for: `docker cp`/`put_archive` is rejected
outright by the daemon — `"container rootfs is marked read-only"` — for
*any* destination path on a container created with `read_only: true`,
regardless of whether that container has started yet or which mount the
destination path resolves to. `L2`'s read-only rootfs and B10's `docker cp`
mechanism are mutually exclusive as `07` describes them; one of the two had
to give, and it is not going to be L2. Delivering the input over the
container's stdin instead sidesteps both problems at once: stdin is a pipe,
not a filesystem write, so it is affected by neither the tmpfs-mount timing
issue nor the read-only rootfs — verified directly against a container
created with every `07` §3 isolation flag set (`read_only`, `network:
none`, non-root, all capabilities dropped) before this module was written
this way. `07` is updated in the same commit as this file (`CLAUDE.md`:
doc drift is a defect).

**The result leaves over stdout too, for the same reason and verified the
same way.** `07` §8's own pseudocode reads `/work/_roottrace/result.json`
*after* `container.wait()` returns — but `/work` is a tmpfs, and a tmpfs's
content does not survive the container's own process exiting; `docker cp`
against a stopped container's tmpfs path fails with "no such file",
confirmed directly, not assumed, the same way the input-side finding was.
Captured stdout/stderr, by contrast, are retained by the container runtime
independent of the container's filesystem lifecycle — `docker logs`
against a stopped container works reliably. `write_result` still writes
the file (useful for a caller with live access to the running container,
e.g. `docker exec`, and cheap to keep), but `emit_result_to_stdout` is
what a caller *outside* the container can actually depend on once it has
exited, delimited so it survives sitting in the same stream as ordinary
gate-subprocess chatter."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

WORK_DIR = Path("/work")
RESULT_PATH = WORK_DIR / "_roottrace" / "result.json"

#: `07` §7's input contract — the keys this process requires to proceed at
#: all. `manifest`, `regression_test`, `new_files`, and `existing_tests` are
#: all meaningful only to gate execution (T6.4), not to materialising a
#: working tree, so they are read but not required here.
_REQUIRED_INPUT_KEYS = (
    "validation_id",
    "language",
    "files_original",
    "files_patched",
    "gates",
    "budgets",
)


class InvalidInputError(Exception):
    """The staged input does not satisfy `07` §7's contract. Raised, never
    silently patched over — a malformed input is this process's caller
    misbehaving, not something to guess around."""


def parse_input(raw: str) -> dict[str, Any]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidInputError(f"input is not valid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise InvalidInputError("input must contain a JSON object")

    missing = [key for key in _REQUIRED_INPUT_KEYS if key not in data]
    if missing:
        raise InvalidInputError(f"input missing required key(s): {missing}")

    return data


def read_input(path: Path) -> dict[str, Any]:
    """File-based reading — used by tests and any future caller that has a
    reason to stage input on disk. The real container entrypoint uses
    `read_input_from_stdin` instead; see the module docstring for why."""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise InvalidInputError(f"no input staged at {path}") from exc
    return parse_input(raw)


def read_input_from_stdin() -> dict[str, Any]:
    return parse_input(sys.stdin.read())


def write_result(result: dict[str, Any], path: Path = RESULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")


#: Delimiters framing the result JSON inside the captured stdout stream —
#: unlikely to collide with anything a gate subprocess (pytest, ruff, mypy)
#: would print on its own, and distinctive enough that a caller scanning the
#: log for them is not depending on the JSON being the last thing printed.
RESULT_STDOUT_START = "===ROOTTRACE_RESULT_START==="
RESULT_STDOUT_END = "===ROOTTRACE_RESULT_END==="


def emit_result_to_stdout(result: dict[str, Any]) -> None:
    """The orchestrator's half of this — parsing the delimited JSON back out
    of a container's captured combined log — lives in
    `apps/worker/roottrace_worker/pipeline/validate/orchestrator.py`, not
    here: `apps/worker` does not import `roottrace_sandbox_runner` (separate
    deployables, one built into the sandbox image and never installed
    outside it, the other running as the worker process — same "duplicated,
    not shared" boundary `apps/worker/roottrace_worker/settings.py` already
    documents for its own reasons). `RESULT_STDOUT_START`/`_END` are
    duplicated as literal constants on both sides rather than shared."""
    print(RESULT_STDOUT_START)
    print(json.dumps(result))
    print(RESULT_STDOUT_END)
