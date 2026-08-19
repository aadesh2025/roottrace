"""S8 sandbox orchestration (`03` §S8, `07` §8, T6.2/T6.3) — create, deliver
input, wait with a hard kill, read the result, always remove.

**Every isolation flag `07` §3 names is set on every `create()` call — T6.2
and T6.3 are not separable at the config level.** A container without
`network_mode="none"`/`read_only`/`user`/`cap_drop` from its very first
creation is not a sandbox at any point in its life; there is no "build the
orchestration first, harden it later" sequencing that does not mean running
untrusted code unconfined in between. What T6.3 actually adds on top of
what is here is the formal 15-item verification suite (`07` §12) proving
each of these flags does what it claims, plus the AppArmor/gVisor pass-
through — see `roottrace_worker/settings.py`'s sandbox fields for the
disclosed gaps (no custom AppArmor profile shipped in V1; gVisor falls
back to `runc` + Docker's own default seccomp profile when unavailable).

**Input travels over stdin after `start()`; the result is parsed out of
captured stdout after `wait()` returns.** Both are corrections to `07`'s
original `docker cp`-based design, made and written up at T6.1 after
testing the original design against a real Docker daemon and finding it
does not work — see `apps/sandbox-runner/roottrace_sandbox_runner/
io_contract.py`'s module docstring and `07` §7 for the full account. The
marker constants below are duplicated from that module rather than
imported, because `apps/worker` does not depend on
`roottrace_sandbox_runner` (separate deployables — one runs inside the
sandbox image, the other never does)."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

import aiodocker
from aiodocker.containers import DockerContainer

from roottrace_worker.pipeline.validate.contracts import (
    ResourceUsage,
    SandboxInput,
    SignalsForScoring,
    Transcript,
    ValidationResult,
)
from roottrace_worker.pipeline.validate.transcript import sanitize, truncate_middle

#: Duplicated from `roottrace_sandbox_runner.io_contract` — see module
#: docstring for why this is a duplication, not a shared import.
RESULT_STDOUT_START = "===ROOTTRACE_RESULT_START==="
RESULT_STDOUT_END = "===ROOTTRACE_RESULT_END==="

#: `07` §3 L7 — the container's entire environment. The worker's own
#: environment (database URLs, LLM keys, GitHub tokens, the Supabase
#: service-role key) never enters; this dict is exhaustive of what *this*
#: orchestrator supplies, not a base to extend from. `PYTHONPATH` is added
#: here (not in `07` §3 L7's literal list) because `apps/sandbox-runner`'s
#: `ENTRYPOINT` (`python -m roottrace_sandbox_runner.runner`) needs it to
#: resolve the package — made an explicit, owned entry rather than left to
#: rely on the base image happening to set the same value (T6.3 found it
#: does, today, but that is the base image's business, not a contract).
SANDBOX_ENV: dict[str, str] = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/work",
    "LANG": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "PYTHONPATH": "/opt",
    "PIP_NO_INDEX": "1",
    "PIP_FIND_LINKS": "/opt/wheels",
    "NPM_CONFIG_OFFLINE": "true",
    "NPM_CONFIG_CACHE": "/opt/npm-cache",
    "CI": "true",
}

#: T6.3 finding: Docker's container `Env` merges with, never replaces, the
#: base image's own baked-in `ENV` entries — there is no Engine API or
#: Dockerfile mechanism to unset an inherited variable, only to overwrite
#: its value under the same name. `python:3.12-slim-bookworm` bakes in
#: `GPG_KEY`/`PYTHON_VERSION`/`PYTHON_SHA256` (public release-signing
#: metadata, not credentials — verified by inspecting their actual values,
#: not assumed from the names); Docker itself always injects `HOSTNAME`.
#: None of the four are secrets, but all four are unaccounted for by
#: `SANDBOX_ENV` above, so the security-verification suite (`07` §12)
#: checks the container's environment against this *explicit allowlist* —
#: `SANDBOX_ENV`'s keys plus these four — rather than the narrower
#: name-pattern regex `07` originally specified, which a variable named
#: `GPG_KEY` would fail on its name alone despite holding no secret.
KNOWN_BASE_IMAGE_ENV_KEYS = frozenset({"HOSTNAME", "GPG_KEY", "PYTHON_VERSION", "PYTHON_SHA256"})

#: `07` §3 L5.
_ULIMITS = (
    {"Name": "nofile", "Soft": 256, "Hard": 512},
    {"Name": "nproc", "Soft": 64, "Hard": 128},
    {"Name": "fsize", "Soft": 67_108_864, "Hard": 67_108_864},
)

_LABEL_VALIDATION_ID = "roottrace.validation_id"
_LABEL_CREATED_AT = "roottrace.created_at"


class SandboxTimeoutError(Exception):
    """`07` §3 L6: the supervisor's hard kill fired. Not a Python exception
    the caller sees — `SandboxOrchestrator.run` catches this internally and
    returns a `failed_gate: "timeout"` `ValidationResult`, matching `03`
    §S8's own "no retries; a gate failure is a result, not an error" rule."""


class ResultExtractionError(Exception):
    """The container's captured output did not contain a well-formed,
    delimited result — the runner crashed before `_finish` ran, produced no
    output at all, or emitted something that fails to parse. Surfaced to
    the caller rather than silently reported as a passing/failing gate,
    since neither is true — the mechanism itself did not complete."""


def _build_create_config(
    *,
    image: str,
    memory_limit_mb: int,
    cpu_limit: float,
    pids_limit: int,
    disk_limit_mb: int,
    runtime: str,
    apparmor_profile: str | None,
    validation_id: str,
) -> dict[str, Any]:
    security_opt = ["no-new-privileges:true"]
    if apparmor_profile:
        # `07` §3 L3: unset leaves Docker's own automatic `docker-default`
        # AppArmor confinement active rather than nothing at all — see
        # `settings.py`'s `sandbox_apparmor_profile` docstring.
        security_opt.append(f"apparmor={apparmor_profile}")

    host_config: dict[str, Any] = {
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "CapDrop": ["ALL"],
        "CapAdd": [],
        "SecurityOpt": security_opt,
        "Privileged": False,
        "Memory": memory_limit_mb * 1024 * 1024,
        "MemorySwap": memory_limit_mb * 1024 * 1024,  # equal to Memory -> swap disabled
        "NanoCpus": int(cpu_limit * 1_000_000_000),
        "PidsLimit": pids_limit,
        # `/work`'s own size is `07` §3 L2's literal figure, independent of
        # `disk_limit_mb` (L5's *container layer* quota, `StorageOpt` below)
        # — a tmpfs is memory-backed, not disk-backed.
        "Tmpfs": {
            "/work": "size=256m,mode=1777",
            "/tmp": "size=64m,mode=1777,noexec",  # noqa: S108 - container-internal mount path
        },
        "StorageOpt": {"size": f"{disk_limit_mb}m"},
        "Ulimits": [dict(limit) for limit in _ULIMITS],
    }
    if runtime == "runsc":
        host_config["Runtime"] = "runsc"

    return {
        "Image": image,
        "User": "65534:65534",
        "OpenStdin": True,
        "StdinOnce": True,
        "AttachStdin": True,
        "AttachStdout": True,
        "AttachStderr": True,
        "Tty": False,
        "Env": [f"{key}={value}" for key, value in SANDBOX_ENV.items()],
        "Labels": {
            _LABEL_VALIDATION_ID: validation_id,
            _LABEL_CREATED_AT: datetime.now(UTC).isoformat(),
        },
        "HostConfig": host_config,
    }


def _extract_result(log_text: str, *, validation_id: str) -> ValidationResult:
    try:
        start = log_text.rindex(RESULT_STDOUT_START) + len(RESULT_STDOUT_START)
        end = log_text.rindex(RESULT_STDOUT_END)
        blob = log_text[start:end]
    except ValueError as exc:
        raise ResultExtractionError(
            f"no result markers found in container output for {validation_id}"
        ) from exc

    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise ResultExtractionError(
            f"result payload for {validation_id} is not valid JSON"
        ) from exc

    return ValidationResult.model_validate(data)


def _timeout_result(validation_id: str, *, wall_ms: int) -> ValidationResult:
    return ValidationResult(
        validation_id=validation_id,
        passed=False,
        mode="full",
        gates=(),
        failed_gate="timeout",
        resource_usage=ResourceUsage(
            wall_ms=wall_ms, cpu_ms=0, peak_memory_mb=0, peak_pids=0, disk_written_mb=0
        ),
        transcript=Transcript(stdout_bytes=0, stderr_bytes=0, truncated=False),
        signals_for_scoring=SignalsForScoring(
            build_passed=False, regression_test_valid=False, test_pass_ratio=None
        ),
        error="hard kill: validation exceeded the timeout budget",
    )


class SandboxOrchestrator:
    def __init__(
        self,
        *,
        docker: aiodocker.Docker,
        image: str,
        timeout_seconds: int = 90,
        memory_limit_mb: int = 512,
        cpu_limit: float = 1.0,
        pids_limit: int = 128,
        disk_limit_mb: int = 256,
        runtime: str = "runsc",
        apparmor_profile: str | None = None,
        max_stdout_bytes: int = 512 * 1024,
        concurrency: int = 4,
    ) -> None:
        self._docker = docker
        self._image = image
        self._timeout_seconds = timeout_seconds
        self._memory_limit_mb = memory_limit_mb
        self._cpu_limit = cpu_limit
        self._pids_limit = pids_limit
        self._disk_limit_mb = disk_limit_mb
        self._runtime = runtime
        self._apparmor_profile = apparmor_profile
        self._max_stdout_bytes = max_stdout_bytes
        self._semaphore = asyncio.Semaphore(concurrency)

    async def run(self, bundle: SandboxInput) -> ValidationResult:
        started = asyncio.get_event_loop().time()

        def wall_ms() -> int:
            return int((asyncio.get_event_loop().time() - started) * 1000)

        config = _build_create_config(
            image=self._image,
            memory_limit_mb=self._memory_limit_mb,
            cpu_limit=self._cpu_limit,
            pids_limit=self._pids_limit,
            disk_limit_mb=self._disk_limit_mb,
            runtime=self._runtime,
            apparmor_profile=self._apparmor_profile,
            validation_id=bundle.validation_id,
        )

        container: DockerContainer | None = None
        try:
            async with self._semaphore:
                container = await self._docker.containers.create(config)
                await container.start()
                await self._write_stdin(container, bundle)

                try:
                    await asyncio.wait_for(container.wait(), timeout=self._timeout_seconds)
                except TimeoutError:
                    with suppress(Exception):
                        await container.kill(signal="SIGKILL")
                    return _timeout_result(bundle.validation_id, wall_ms=wall_ms())

                raw_log = "".join(await container.log(stdout=True, stderr=True))
                return _extract_result(raw_log, validation_id=bundle.validation_id)
        finally:
            if container is not None:
                with suppress(Exception):
                    await container.delete(force=True, v=True)

    async def _write_stdin(self, container: DockerContainer, bundle: SandboxInput) -> None:
        stream = container.attach(stdin=True, stdout=True, stderr=True)
        try:
            await stream.write_in(bundle.model_dump_json().encode("utf-8"))
        finally:
            await stream.close()

    def sanitize_transcript(self, raw_log: str) -> Transcript:
        """Not called by `run` above — `run` only needs the delimited
        result, not the full transcript — but exposed for a caller (T6.4's
        gate results, eventually persisted `transcript_url`) that wants the
        sanitised, capped text `07` §7 describes, separate from parsing the
        result JSON out of it."""
        clean = sanitize(raw_log)
        truncated = truncate_middle(clean, max_bytes=self._max_stdout_bytes)
        return Transcript(
            stdout_bytes=truncated.original_bytes, stderr_bytes=0, truncated=truncated.truncated
        )


class SandboxReaper:
    """`07` §3 L8 / §8: "any container labelled `roottrace.validation_id`
    older than 120 s is force-removed regardless of state." Independent of
    `SandboxOrchestrator.run`'s own `finally`-block removal — that handles
    the normal case; this handles the container whose supervising worker
    process itself died mid-validation and never reached its own
    `finally`."""

    def __init__(self, *, docker: aiodocker.Docker, orphan_max_age_seconds: int = 120) -> None:
        self._docker = docker
        self._orphan_max_age_seconds = orphan_max_age_seconds

    async def reap_once(self) -> tuple[str, ...]:
        containers = await self._docker.containers.list(
            all=True, filters=json.dumps({"label": [_LABEL_VALIDATION_ID]})
        )
        now = datetime.now(UTC)
        reaped = []
        for container in containers:
            info = await container.show()
            created = datetime.fromisoformat(info["Created"])
            age_seconds = (now - created).total_seconds()
            if age_seconds > self._orphan_max_age_seconds:
                with suppress(Exception):
                    await container.delete(force=True, v=True)
                reaped.append(container.id)
        return tuple(reaped)

    async def run_forever(self, *, interval_seconds: int = 60) -> None:  # pragma: no cover
        while True:
            await self.reap_once()
            await asyncio.sleep(interval_seconds)
