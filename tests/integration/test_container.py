"""The `api` container (T1.5 acceptance, `13` §3).

"Container builds and runs as non-root. Health checks respond." Hand-running
`docker build` proves that once; this proves it on every commit, which is the
difference between a property and an anecdote.

The image is built here rather than assumed present, because the defect this
suite exists to catch is a Dockerfile that stops producing a working image —
and a suite that skips when the image is missing would go quiet at exactly the
moment it mattered.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import httpx
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.security]

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "infra" / "docker" / "api.Dockerfile"
IMAGE = "roottrace-api:test"
PORT = 18099

CONTAINER_ENV = {
    "RT_ENVIRONMENT": "local",
    "RT_VERSION": "container-test",
    "RT_SERVICE_NAME": "api",
    "RT_DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
    "RT_SUPABASE_URL": "http://127.0.0.1:54321",
    "RT_SUPABASE_ANON_KEY": "anon-REPLACE_ME",
    "RT_SUPABASE_JWKS_URL": "http://127.0.0.1:54321/auth/v1/.well-known/jwks.json",
}


def _require_docker() -> None:
    """Fail, rather than skip, when docker is absent.

    A `skipif` here would go green on any machine where docker disappeared —
    including CI — and this suite is the only automated proof that the image
    builds and runs as non-root. Docker is already a hard requirement: the rest
    of the integration suite needs it to run Supabase at all (`13` §3).
    """
    if shutil.which("docker") is None:
        raise AssertionError("docker is not installed; `13` §3 requires it")


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
        args, capture_output=True, text=True, check=check, timeout=900
    )


@pytest.fixture(scope="module")
def image() -> str:
    _require_docker()
    _run("docker", "build", "-f", str(DOCKERFILE), "-t", IMAGE, str(REPO_ROOT))
    return IMAGE


@pytest.fixture(scope="module")
def running(image: str) -> str:
    """A started container, torn down whatever happens."""
    name = f"rt-api-test-{uuid.uuid4().hex[:8]}"
    argv = ["docker", "run", "-d", "--name", name, "-p", f"{PORT}:8000"]
    for key, value in CONTAINER_ENV.items():
        argv += ["-e", f"{key}={value}"]
    argv.append(image)
    _run(*argv)

    try:
        _wait_for_health()
        yield name
    finally:
        _run("docker", "rm", "-f", name, check=False)


def _wait_for_health() -> None:
    """Poll until the server answers.

    A poll loop, not a sleep: the wait is bounded by the condition rather than
    by a guess, so it is neither flaky on a slow machine nor slow on a fast one
    (CLAUDE.md — no `sleep()` in tests).
    """
    deadline = time.monotonic() + 60
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError as exc:
            last = exc
        time.sleep(0.25)
    raise AssertionError(f"container never became healthy: {last}")


def test_the_container_runs_as_a_non_root_fixed_uid(image: str) -> None:
    """`13` §3. Root in a container is one escape away from root on the host,
    and a fixed UID keeps that true across base-image rebuilds."""
    assert (
        _run("docker", "run", "--rm", "--entrypoint", "id", image, "-u").stdout.strip() == "10001"
    )
    assert _run("docker", "run", "--rm", "--entrypoint", "id", image, "-un").stdout.strip() == "app"


def test_the_health_endpoints_respond(running: str) -> None:
    health = httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=5)
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    ready = httpx.get(f"http://127.0.0.1:{PORT}/health/ready", timeout=5)
    assert ready.status_code == 200
    assert ready.json()["deployment_tier"] == "evaluation"


def test_responses_carry_a_request_id_and_security_headers(running: str) -> None:
    """Through a real ASGI server rather than TestClient — the header rewriting
    happens in the `send` chain, which is the part TestClient stubs least."""
    response = httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=5)

    assert response.headers["x-request-id"].startswith("req_")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_container_logs_are_structured_json(running: str) -> None:
    """A log line the platform aggregator cannot parse is not a log line
    (`12` §1). Verified against what the container actually emits, including
    uvicorn's own startup lines, which do not go through structlog."""
    httpx.get(f"http://127.0.0.1:{PORT}/health", timeout=5)
    logs = _run("docker", "logs", running).stdout + _run("docker", "logs", running).stderr

    raw = [line for line in logs.splitlines() if line.strip()]
    assert raw, "the container logged nothing"

    # Every line, including the boot lines uvicorn emits before the app exists.
    # Those escaped the chain when the entrypoint was `uvicorn --factory`; the
    # explicit `serve.py` entrypoint is what closed that window.
    unparseable = [line for line in raw if not line.lstrip().startswith("{")]
    assert not unparseable, f"non-JSON log lines: {unparseable[:3]}"

    lines = [json.loads(line) for line in raw]
    assert all("timestamp" in line and "level" in line for line in lines)

    # uvicorn's own output, routed through our chain rather than escaping it.
    assert any(line["logger"].startswith("uvicorn") for line in lines)
    # Exactly one request line per request — uvicorn's access log is silenced
    # because ours carries the request id and the duration and its does not.
    request_lines = [line for line in lines if line["message"] == "http_request"]
    assert request_lines
    assert not [line for line in lines if line["logger"] == "uvicorn.access"]
    assert request_lines[-1]["duration_ms"] >= 0
    assert request_lines[-1]["request_id"].startswith("req_")


def test_no_secret_is_baked_into_an_image_layer(image: str) -> None:
    """SC28. A secret removed in a later layer is still readable in the one
    that added it, so this inspects the build history rather than the
    filesystem."""
    history = _run("docker", "history", "--no-trunc", "--format", "{{.CreatedBy}}", image).stdout

    for marker in ("RT_SUPABASE_ANON_KEY=", "SERVICE_ROLE", "PRIVATE KEY", "rt_live_"):
        assert marker not in history, f"{marker} appears in an image layer"
