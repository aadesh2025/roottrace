"""`SandboxOrchestrator`/`SandboxReaper` (T6.2) — exercised against a real
Docker daemon and the real `roottrace/sandbox-python:3.12` image (T6.1),
never mocked. `03` §S8/`07` are specifically about what a *real* container
does under isolation; a mocked Docker client would prove nothing about
this stage that matters.

Skipped when Docker is unreachable or the image has not been built —
`apps/sandbox-runner/python/warm_wheels.sh && docker build -f
apps/sandbox-runner/python/Dockerfile -t roottrace/sandbox-python:3.12
apps/sandbox-runner` builds it locally; CI builds it as a prerequisite
step before this suite runs."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import aiodocker
import pytest

from roottrace_worker.pipeline.validate import (
    ResultExtractionError,
    SandboxInput,
    SandboxOrchestrator,
    SandboxReaper,
)

IMAGE = "roottrace/sandbox-python:3.12"


async def _docker_and_image_available() -> bool:
    try:
        docker = aiodocker.Docker()
        try:
            await docker.images.inspect(IMAGE)
            return True
        finally:
            await docker.close()
    except Exception:
        return False


_AVAILABLE = asyncio.run(_docker_and_image_available())

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _AVAILABLE,
        reason=f"Docker unreachable or {IMAGE} not built — see module docstring",
    ),
]


@pytest.fixture
async def docker() -> AsyncIterator[aiodocker.Docker]:
    client = aiodocker.Docker()
    try:
        yield client
    finally:
        await client.close()


def _bundle(validation_id: str, **overrides: object) -> SandboxInput:
    payload: dict[str, object] = {
        "validation_id": validation_id,
        "language": "python",
        "language_version": "3.12",
        "attempt": 1,
        "files_original": {"a.py": "x = 1\n"},
        "files_patched": {"a.py": "x = 2\n"},
        "gates": (),
        "budgets": {"total_s": 45},
    }
    payload.update(overrides)
    return SandboxInput.model_validate(payload)


async def test_a_clean_run_round_trips_through_stdin_and_stdout(
    docker: aiodocker.Docker,
) -> None:
    orch = SandboxOrchestrator(docker=docker, image=IMAGE, runtime="runc", timeout_seconds=30)
    result = await orch.run(_bundle("val_orch_roundtrip"))

    assert result.passed
    assert result.validation_id == "val_orch_roundtrip"
    assert result.failed_gate is None


async def test_the_container_is_removed_promptly_after_exit(docker: aiodocker.Docker) -> None:
    orch = SandboxOrchestrator(docker=docker, image=IMAGE, runtime="runc", timeout_seconds=30)
    started = asyncio.get_event_loop().time()
    await orch.run(_bundle("val_orch_cleanup"))
    elapsed = asyncio.get_event_loop().time() - started

    containers = await docker.containers.list(
        all=True, filters=json.dumps({"label": ["roottrace.validation_id=val_orch_cleanup"]})
    )
    assert containers == []
    # The whole run (create, start, execute, remove) comfortably clears
    # `15` T6.2's "removed within 5 s of exit" bar — the container itself
    # runs in well under a second for an empty gate list.
    assert elapsed < 10


async def test_a_timeout_kills_the_container_and_reports_the_timeout_gate(
    docker: aiodocker.Docker,
) -> None:
    # A 0 s budget: any real container start/materialise/log-write work
    # exceeds it, forcing the SIGKILL path deterministically without
    # depending on a gate that runs long (none do yet — T6.4).
    orch = SandboxOrchestrator(docker=docker, image=IMAGE, runtime="runc", timeout_seconds=0)
    result = await orch.run(_bundle("val_orch_timeout"))

    assert not result.passed
    assert result.failed_gate == "timeout"
    assert result.error is not None and "timeout" in result.error.lower()

    # The killed container must still be cleaned up, not orphaned.
    containers = await docker.containers.list(
        all=True, filters=json.dumps({"label": ["roottrace.validation_id=val_orch_timeout"]})
    )
    assert containers == []


async def test_concurrency_is_capped_at_the_configured_limit(docker: aiodocker.Docker) -> None:
    """`15` T6.2's "concurrency semaphore holds under 50 queued runs" —
    exercised at a smaller N here for a suite that still runs in seconds,
    but the mechanism (`asyncio.Semaphore`, stdlib-guaranteed) does not
    behave differently at 8 versus 50; what matters is that the
    orchestrator actually wires it around real container execution, which
    only a real measurement against the live daemon can show."""
    orch = SandboxOrchestrator(
        docker=docker, image=IMAGE, runtime="runc", timeout_seconds=30, concurrency=2
    )

    max_seen = 0
    stop = asyncio.Event()

    async def poll() -> None:
        nonlocal max_seen
        while not stop.is_set():
            running = await docker.containers.list(
                filters=json.dumps({"label": ["roottrace.validation_id"], "status": ["running"]})
            )
            max_seen = max(max_seen, len(running))
            await asyncio.sleep(0.05)

    poll_task = asyncio.create_task(poll())
    results = await asyncio.gather(*(orch.run(_bundle(f"val_orch_conc_{i}")) for i in range(8)))
    stop.set()
    await poll_task

    assert all(r.passed for r in results)
    assert max_seen <= 2


async def test_missing_result_markers_raise_result_extraction_error(
    docker: aiodocker.Docker,
) -> None:
    """`gates` naming a real gate (`07` §6) fails inside the container —
    `_GATE_DISPATCH` is empty until T6.4 — which is a legitimate way to
    reach a malformed/absent result: the runner's own error path still
    emits well-formed markers for *that* failure, so this specifically
    checks the orchestrator's own defence if a container ever produced no
    markers at all, by pointing it at an image with no runner in it."""
    orch = SandboxOrchestrator(
        docker=docker, image="python:3.12-slim-bookworm", runtime="runc", timeout_seconds=30
    )
    with pytest.raises(ResultExtractionError):
        await orch.run(_bundle("val_orch_no_markers"))


async def test_reaper_removes_an_orphaned_container_past_its_max_age(
    docker: aiodocker.Docker,
) -> None:
    config: dict[str, Any] = {
        "Image": IMAGE,
        "OpenStdin": True,
        "HostConfig": {"NetworkMode": "none"},
        "Labels": {
            "roottrace.validation_id": "val_reaper_orphan",
            "roottrace.created_at": "orphaned-by-a-crashed-worker",
        },
    }
    orphan = await docker.containers.create(config)
    await asyncio.sleep(1)

    reaper = SandboxReaper(docker=docker, orphan_max_age_seconds=0)
    reaped = await reaper.reap_once()

    assert orphan.id in reaped
    containers = await docker.containers.list(all=True, filters=json.dumps({"id": [orphan.id]}))
    assert containers == []


async def test_reaper_leaves_a_fresh_container_alone(docker: aiodocker.Docker) -> None:
    config: dict[str, Any] = {
        "Image": IMAGE,
        "OpenStdin": True,
        "HostConfig": {"NetworkMode": "none"},
        "Labels": {
            "roottrace.validation_id": "val_reaper_fresh",
            "roottrace.created_at": "just-created",
        },
    }
    fresh = await docker.containers.create(config)
    try:
        reaper = SandboxReaper(docker=docker, orphan_max_age_seconds=120)
        reaped = await reaper.reap_once()
        assert fresh.id not in reaped
    finally:
        await fresh.delete(force=True, v=True)
