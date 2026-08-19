"""`07` §12's security verification checklist (T6.3), each item as a real,
automated assertion against a real Docker daemon and the real
`roottrace/sandbox-python:3.12` image — never mocked, for the same reason
`test_validate_orchestrator.py` gives: this is specifically about what a
*real* container does under isolation.

Marked `security` (`14` §3's registered marker — "RLS, sandbox isolation,
injection corpus"), run via `make test-security`. Skipped when Docker is
unreachable or the image has not been built — see
`test_validate_orchestrator.py`'s module docstring for the build command.

Every probe here overrides the image's `ENTRYPOINT`/`Cmd` to run a small,
targeted Python (or shell) snippet instead of the real runner — these
tests are about the *isolation layer* `_build_create_config` produces,
not about `roottrace_sandbox_runner`'s own business logic, which
`test_validate_orchestrator.py` and `apps/sandbox-runner/tests/` already
cover."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import aiodocker
import pytest

from roottrace_worker.pipeline.validate import SandboxInput, SandboxOrchestrator
from roottrace_worker.pipeline.validate.orchestrator import (
    KNOWN_BASE_IMAGE_ENV_KEYS,
    SANDBOX_ENV,
    _build_create_config,
)
from roottrace_worker.pipeline.validate.transcript import sanitize, truncate_middle

IMAGE = "roottrace/sandbox-python:3.12"

#: `A3` §"Supabase"/"LLM" — the worker's actual credential-bearing fields.
#: The one invariant `07` §3 L7 genuinely cares about: none of these names
#: is ever present inside the sandbox, regardless of value.
WORKER_SECRET_ENV_NAMES = (
    "RT_SUPABASE_SERVICE_ROLE_KEY",
    "RT_ANTHROPIC_API_KEY",
    "RT_OPENAI_API_KEY",
    "RT_VOYAGE_API_KEY",
    "RT_DATABASE_URL",
    "RT_REDIS_URL",
    "RT_SUPABASE_URL",
    "RT_GITHUB_PRIVATE_KEY",
    "RT_GITHUB_WEBHOOK_SECRET",
    "RT_GITHUB_CLIENT_SECRET",
)


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
    pytest.mark.security,
    pytest.mark.skipif(
        not _AVAILABLE,
        reason=f"Docker unreachable or {IMAGE} not built — see test_validate_orchestrator.py",
    ),
]


@pytest.fixture
async def docker() -> AsyncIterator[aiodocker.Docker]:
    client = aiodocker.Docker()
    try:
        yield client
    finally:
        await client.close()


async def _probe(
    docker: aiodocker.Docker,
    *,
    entrypoint: list[str],
    cmd: list[str],
    memory_limit_mb: int = 512,
    pids_limit: int = 128,
    timeout_seconds: float = 10,
) -> tuple[int | None, bool, str]:
    """Runs one command under the exact isolation profile
    `SandboxOrchestrator` uses — reuses `_build_create_config` itself
    rather than a hand-rolled copy, so these tests verify the actual
    function `SandboxOrchestrator.run` calls, not a parallel
    reimplementation of it that could quietly drift from it.

    Returns `(exit_code, oom_killed, combined_log)`."""
    config = _build_create_config(
        image=IMAGE,
        memory_limit_mb=memory_limit_mb,
        cpu_limit=1.0,
        pids_limit=pids_limit,
        disk_limit_mb=256,
        runtime="runc",
        apparmor_profile=None,
        validation_id="security-probe",
    )
    config["Entrypoint"] = entrypoint
    config["Cmd"] = cmd

    container = await docker.containers.create(config)
    try:
        await container.start()
        try:
            await asyncio.wait_for(container.wait(), timeout=timeout_seconds)
        except TimeoutError:
            await container.kill(signal="SIGKILL")
        info = await container.show()
        exit_code = info["State"]["ExitCode"]
        oom_killed = bool(info["State"].get("OOMKilled", False))
        log = "".join(await container.log(stdout=True, stderr=True))
        return exit_code, oom_killed, log
    finally:
        await container.delete(force=True, v=True)


async def test_dns_resolution_fails(docker: aiodocker.Docker) -> None:
    exit_code, _oom, log = await _probe(
        docker,
        entrypoint=["python", "-c"],
        cmd=["import socket; socket.getaddrinfo('example.com', 80)"],
    )
    assert exit_code != 0
    assert "socket.gaierror" in log or "Name or service not known" in log or "Errno" in log


async def test_tcp_socket_cannot_reach_any_address(docker: aiodocker.Docker) -> None:
    exit_code, _oom, log = await _probe(
        docker,
        entrypoint=["python", "-c"],
        cmd=["import socket; s = socket.socket(); s.settimeout(3); s.connect(('8.8.8.8', 53))"],
    )
    assert exit_code != 0
    assert "Network is unreachable" in log or "Errno" in log


async def test_environment_matches_the_explicit_allowlist_no_worker_secret_present(
    docker: aiodocker.Docker,
) -> None:
    _exit_code, _oom, log = await _probe(
        docker, entrypoint=["python", "-c"], cmd=["import os\nfor k in os.environ: print(k)"]
    )
    keys = {line.strip() for line in log.splitlines() if line.strip()}

    allowed = set(SANDBOX_ENV) | KNOWN_BASE_IMAGE_ENV_KEYS
    unexpected = keys - allowed
    assert not unexpected, f"unaccounted-for environment variable(s): {unexpected}"

    for secret_name in WORKER_SECRET_ENV_NAMES:
        assert secret_name not in keys


async def test_writing_to_etc_fails_with_erofs(docker: aiodocker.Docker) -> None:
    exit_code, _oom, log = await _probe(
        docker, entrypoint=["python", "-c"], cmd=["open('/etc/test_write', 'w')"]
    )
    assert exit_code != 0
    assert "Read-only file system" in log


async def test_work_is_tmpfs_and_does_not_persist_across_two_containers(
    docker: aiodocker.Docker,
) -> None:
    await _probe(
        docker,
        entrypoint=["sh", "-c"],
        cmd=["echo leftover > /work/leftover.txt"],
    )
    # A second, independent container gets its own fresh tmpfs — nothing
    # the first container wrote is visible here.
    exit_code, _oom, log = await _probe(
        docker,
        entrypoint=["sh", "-c"],
        cmd=["test -e /work/leftover.txt && echo FOUND || echo NOT_FOUND"],
    )
    assert exit_code == 0
    assert "NOT_FOUND" in log


async def test_process_runs_as_uid_65534(docker: aiodocker.Docker) -> None:
    exit_code, _oom, log = await _probe(
        docker, entrypoint=["python", "-c"], cmd=["import os; print(os.getuid())"]
    )
    assert exit_code == 0
    assert log.strip() == "65534"


async def test_mount_syscall_returns_eperm(docker: aiodocker.Docker) -> None:
    exit_code, _oom, log = await _probe(
        docker,
        entrypoint=["python", "-c"],
        cmd=[
            "import ctypes\n"
            "libc = ctypes.CDLL(None, use_errno=True)\n"
            "r = libc.mount(b'none', b'/tmp', b'tmpfs', 0, None)\n"
            "print(r, ctypes.get_errno())"
        ],
    )
    assert exit_code == 0
    ret, errno = log.strip().split()
    assert ret == "-1"
    assert errno == "1"  # EPERM


async def test_unshare_syscall_returns_eperm(docker: aiodocker.Docker) -> None:
    exit_code, _oom, log = await _probe(
        docker,
        entrypoint=["python", "-c"],
        cmd=[
            "import ctypes\n"
            "libc = ctypes.CDLL(None, use_errno=True)\n"
            "CLONE_NEWNS = 0x00020000\n"
            "r = libc.unshare(CLONE_NEWNS)\n"
            "print(r, ctypes.get_errno())"
        ],
    )
    assert exit_code == 0
    ret, errno = log.strip().split()
    assert ret == "-1"
    assert errno == "1"  # EPERM


async def test_ptrace_attach_against_another_process_returns_eperm(
    docker: aiodocker.Docker,
) -> None:
    """`PTRACE_ATTACH` (16) against pid 1 — the meaningful ptrace check.
    `PTRACE_TRACEME` (0) against *itself* is deliberately not tested here:
    that request needs no elevated capability on any Linux system,
    sandboxed or not (it only registers self-traceability for a parent),
    so a permissive result from it is not a finding."""
    exit_code, _oom, log = await _probe(
        docker,
        entrypoint=["python", "-c"],
        cmd=[
            "import ctypes\n"
            "libc = ctypes.CDLL(None, use_errno=True)\n"
            "PTRACE_ATTACH = 16\n"
            "r = libc.ptrace(PTRACE_ATTACH, 1, None, None)\n"
            "print(r, ctypes.get_errno())"
        ],
    )
    assert exit_code == 0
    ret, errno = log.strip().split()
    assert ret == "-1"
    assert errno == "1"  # EPERM


async def test_fork_bomb_is_contained_by_pids_limit(docker: aiodocker.Docker) -> None:
    exit_code, _oom, log = await _probe(
        docker,
        entrypoint=["python", "-c"],
        cmd=[
            "import os\n"
            "n = 0\n"
            "while True:\n"
            "    try:\n"
            "        os.fork()\n"
            "        n += 1\n"
            "    except OSError:\n"
            "        break\n"
            "print('stopped after', n, 'forks')"
        ],
        pids_limit=20,
        timeout_seconds=15,
    )
    # The container completes on its own (the fork loop hits EAGAIN and
    # breaks) rather than being killed — proof the limit contains the
    # bomb without the supervisor needing to intervene.
    assert exit_code == 0
    assert "stopped after" in log


async def test_an_over_limit_allocation_is_oom_killed(docker: aiodocker.Docker) -> None:
    exit_code, oom_killed, _log = await _probe(
        docker,
        entrypoint=["python", "-c"],
        cmd=["bytearray(512 * 1024 * 1024)"],
        memory_limit_mb=64,
        timeout_seconds=15,
    )
    assert oom_killed
    assert exit_code == 137


async def test_an_infinite_loop_is_sigkilled_at_the_timeout(docker: aiodocker.Docker) -> None:
    exit_code, _oom, _log = await _probe(
        docker, entrypoint=["python", "-c"], cmd=["while True: pass"], timeout_seconds=3
    )
    assert exit_code == 137


async def test_no_unexpected_host_path_is_visible_under_mountinfo(
    docker: aiodocker.Docker,
) -> None:
    exit_code, _oom, log = await _probe(docker, entrypoint=["cat"], cmd=["/proc/self/mountinfo"])
    assert exit_code == 0

    expected_mount_points = {
        "/",
        "/proc",
        "/dev",
        "/dev/pts",
        "/sys",
        "/sys/fs/cgroup",
        "/dev/mqueue",
        "/dev/shm",  # noqa: S108 - a container-internal mount point being enumerated, not used
        "/tmp",  # noqa: S108 - container-internal mount point
        "/work",
        "/etc/resolv.conf",
        "/etc/hostname",
        "/etc/hosts",
        "/proc/bus",
        "/proc/fs",
        "/proc/irq",
        "/proc/sys",
        "/proc/sysrq-trigger",
        "/proc/acpi",
        "/proc/interrupts",
        "/proc/kcore",
        "/proc/keys",
        "/proc/latency_stats",
        "/proc/scsi",
        "/proc/timer_list",
        "/sys/firmware",
    }
    for line in log.splitlines():
        fields = line.split()
        if len(fields) <= 4:
            continue
        mount_point = fields[4]
        assert mount_point in expected_mount_points, f"unexpected mount point: {mount_point}"


async def test_container_is_removed_promptly_and_reaper_catches_orphans(
    docker: aiodocker.Docker,
) -> None:
    pytest.skip(
        "covered by test_validate_orchestrator.py::"
        "test_the_container_is_removed_promptly_after_exit and "
        "::test_reaper_removes_an_orphaned_container_past_its_max_age (T6.2) — "
        "recorded here, not duplicated, so 07 SS12's own checklist has a named "
        "test for every item without asserting the same thing twice"
    )


async def test_two_concurrent_validations_cannot_observe_each_others_work(
    docker: aiodocker.Docker,
) -> None:
    orch = SandboxOrchestrator(docker=docker, image=IMAGE, runtime="runc", timeout_seconds=20)
    bundle_a = SandboxInput(
        validation_id="val_isolation_a",
        language="python",
        language_version="3.12",
        attempt=1,
        files_original={"secret_a.py": "A = 1\n"},
        files_patched={"secret_a.py": "A = 1\n"},
        gates=(),
        budgets={"total_s": 45},
    )
    bundle_b = SandboxInput(
        validation_id="val_isolation_b",
        language="python",
        language_version="3.12",
        attempt=1,
        files_original={"secret_b.py": "B = 1\n"},
        files_patched={"secret_b.py": "B = 1\n"},
        gates=(),
        budgets={"total_s": 45},
    )
    result_a, result_b = await asyncio.gather(orch.run(bundle_a), orch.run(bundle_b))
    assert result_a.passed
    assert result_b.passed

    exit_code, _oom, log = await _probe(
        docker,
        entrypoint=["sh", "-c"],
        cmd=["ls /work"],
    )
    assert exit_code == 0
    # Neither bundle's files exist in a *third*, freshly created
    # container's /work — each container's tmpfs is its own, never shared.
    assert "secret_a.py" not in log
    assert "secret_b.py" not in log


async def test_transcript_truncation_and_sanitisation_against_real_captured_output(
    docker: aiodocker.Docker,
) -> None:
    exit_code, _oom, log = await _probe(
        docker,
        entrypoint=["python", "-c"],
        cmd=["print('\\x1b[31m' + 'x' * 5000 + '\\x1b[0m')"],
    )
    assert exit_code == 0

    # `sanitize_transcript` counts bytes *after* sanitising — the raw log
    # still has the ANSI escapes in it, so the two byte counts legitimately
    # differ by exactly what got stripped.
    clean = sanitize(log)
    assert "\x1b[" not in clean
    assert len(clean.encode("utf-8")) < len(log.encode("utf-8"))

    orch = SandboxOrchestrator(docker=docker, image=IMAGE, max_stdout_bytes=1000)
    transcript = orch.sanitize_transcript(log)
    assert transcript.truncated
    assert transcript.stdout_bytes == len(clean.encode("utf-8"))
    result = truncate_middle(clean, max_bytes=1000)
    assert result.truncated
    assert len(result.text.encode("utf-8")) < len(clean.encode("utf-8"))


async def test_uncached_package_install_fails_offline(docker: aiodocker.Docker) -> None:
    exit_code, _oom, log = await _probe(
        docker,
        entrypoint=["sh", "-c"],
        cmd=[
            "pip install --no-index --find-links=/opt/wheels "
            "this-package-does-not-exist-in-any-registry-xyz"
        ],
    )
    assert exit_code != 0
    assert "No matching distribution found" in log
