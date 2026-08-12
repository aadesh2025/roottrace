"""The standard error envelope, `request_id`, and security headers (T1.5).

T1.5's acceptance: "A deliberate exception produces the standard error envelope
with `request_id`, and the log line contains no secrets."
"""

from __future__ import annotations

import io
import json
import re
from collections.abc import Callable
from pathlib import Path

import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, HttpUrl

from roottrace_api.errors import ERROR_CODES, ApiError, error_response
from roottrace_api.ids import REQUEST_ID_PREFIX
from roottrace_api.log import REDACTED, configure_logging
from roottrace_api.main import create_app
from roottrace_api.settings import Settings

#: Supplied by conftest.py as a fixture rather than imported — `apps/api/tests`
#: is not a package.
SettingsFactory = Callable[..., Settings]

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[3]

# A credential in a real format, raised from inside a handler.
LEAKED_DSN = "postgresql://postgres:hunter2-REPLACE_ME@db.example.test:5432/postgres"


class _Body(BaseModel):
    count: int


@pytest.fixture
def logs(make_settings: SettingsFactory) -> io.StringIO:
    stream = io.StringIO()
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    configure_logging(make_settings(), stream=stream)
    return stream


@pytest.fixture
def app(logs: io.StringIO, make_settings: SettingsFactory) -> FastAPI:
    """The real app plus routes that fail on purpose.

    Added to a freshly built app rather than a shared module-level one, which
    is why `create_app` is a factory.
    """
    application = create_app(make_settings())

    @application.get("/boom")
    def boom() -> None:
        # The kind of failure nobody plans for: an exception carrying a
        # credential in its message.
        raise RuntimeError(f"connection refused: {LEAKED_DSN}")

    @application.get("/known-failure")
    def known_failure() -> None:
        raise ApiError("RT-NOTFOUND-0001", "Investigation not found")

    @application.post("/validated")
    def validated(body: _Body) -> dict[str, int]:
        return {"count": body.count}

    # Re-install the buffer: create_app configures logging against stdout.
    configure_logging(make_settings(), stream=logs)
    return application


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    # raise_server_exceptions=False makes TestClient behave like a real server:
    # the exception reaches our handler instead of being re-raised into the
    # test, which is the path a client actually experiences.
    return TestClient(app, raise_server_exceptions=False)


# ── The acceptance criterion ───────────────────────────────────────────────


def test_a_deliberate_exception_produces_the_envelope(client: TestClient) -> None:
    response = client.get("/boom")

    assert response.status_code == 500
    error = response.json()["error"]
    assert error["code"] == "RT-INTERNAL-0001"
    assert error["request_id"].startswith(REQUEST_ID_PREFIX)
    assert error["documentation_url"].endswith("/RT-INTERNAL-0001")
    assert set(response.json()) == {"error"}


def test_the_500_body_does_not_echo_the_exception(client: TestClient) -> None:
    """`str(exc)` on an unhandled failure is routinely a connection string or a
    query with values inlined. This is the one response path where nobody has
    decided what is safe to say, so nothing from the exception is said."""
    body = client.get("/boom").text

    assert "hunter2-REPLACE_ME" not in body
    assert "connection refused" not in body


def test_the_log_line_for_that_exception_contains_no_secret(
    client: TestClient, logs: io.StringIO
) -> None:
    """The second half of the acceptance criterion. The traceback IS logged —
    an operator needs it — and the redaction processor is what makes that
    safe."""
    client.get("/boom")
    output = logs.getvalue()

    assert "unhandled_exception" in output
    assert "hunter2-REPLACE_ME" not in output
    assert REDACTED in output


def test_the_response_header_and_the_body_agree(client: TestClient) -> None:
    """An operator reads the id off the header; the user quotes it from the
    body. If they differ, the id correlates nothing."""
    response = client.get("/boom")
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


def test_the_log_line_carries_the_same_request_id(client: TestClient, logs: io.StringIO) -> None:
    """The whole point of `12` §2.1: a user saying "it broke" and quoting an id
    leads to the exact log lines."""
    request_id = client.get("/boom").headers["x-request-id"]
    lines = [json.loads(line) for line in logs.getvalue().splitlines() if line.strip()]

    correlated = [line for line in lines if line.get("request_id") == request_id]
    assert {line["message"] for line in correlated} >= {"unhandled_exception", "http_request"}


def test_each_request_gets_a_distinct_id(client: TestClient) -> None:
    ids = {client.get("/health").headers["x-request-id"] for _ in range(20)}
    assert len(ids) == 20


def test_an_inbound_request_id_header_is_ignored(client: TestClient) -> None:
    """It is attacker-controlled text arriving on the public internet, landing
    in the field every log line, error body and (from `12` §2.1) queued job is
    keyed on. Adopting it would let a caller forge collisions with another
    tenant's requests or inject newlines into log lines."""
    forged = "req_" + "0" * 32
    response = client.get("/health", headers={"X-Request-ID": forged})

    assert response.headers["x-request-id"] != forged


# ── The rest of the envelope ───────────────────────────────────────────────


def test_a_known_failure_keeps_its_registered_code(client: TestClient) -> None:
    response = client.get("/known-failure")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RT-NOTFOUND-0001"


def test_validation_failures_report_fields(client: TestClient) -> None:
    """`05` §3's `details` array, with `field` naming what the client sent."""
    response = client.post("/validated", json={"count": "not-a-number"})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "RT-VALIDATION-0001"
    assert error["details"][0]["field"] == "count"
    assert "request_id" in error


def test_an_unrouted_path_still_gets_the_envelope(client: TestClient) -> None:
    """Starlette's own 404 would otherwise return `{"detail": ...}` — a second
    error shape from the same API."""
    response = client.get("/no-such-path")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RT-NOTFOUND-0001"


def test_a_wrong_method_still_gets_the_envelope(client: TestClient) -> None:
    """405 had no registered code, so it escaped the envelope entirely.
    `RT-VALIDATION-0002` was registered in `17` §4 rather than reusing an
    unrelated one."""
    response = client.post("/health")
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "RT-VALIDATION-0002"


def test_a_401_keeps_its_www_authenticate_header(client: TestClient) -> None:
    """Headers set by the raiser survive the envelope. Dropping this one would
    break the challenge/response contract for every HTTP client."""
    response = client.get("/v1/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "RT-AUTH-0001"
    assert response.headers["www-authenticate"] == "Bearer"


def test_an_unregistered_code_cannot_be_emitted() -> None:
    """The mechanical form of "don't invent unregistered codes" (CLAUDE.md).
    A convention would be followed until the first hurried afternoon."""
    with pytest.raises(KeyError, match="not in the error registry"):
        error_response("RT-MADE-UP-9999", "nope")
    with pytest.raises(KeyError, match="not in the error registry"):
        ApiError("RT-MADE-UP-9999", "nope")


# ── Drift ──────────────────────────────────────────────────────────────────


def test_error_code_registry_matches_docs() -> None:
    """`17` §4 owns the registry; this module holds the runtime copy.

    Compared in both directions, so a code added to one and not the other is a
    failing test rather than drift found months later. Codes whose HTTP column
    is "—" are pipeline terminal states and boot failures — outcomes recorded
    in the database, never responses — and are excluded on purpose.
    """
    glossary = (REPO_ROOT / "docs" / "17-GLOSSARY.md").read_text(encoding="utf-8")
    section = glossary.split("## 4. Error code registry", 1)[1].split("\n---", 1)[0]

    documented = {
        match.group(1): int(match.group(2))
        for match in re.finditer(r"^\|\s*`(RT-[A-Z]+-\d+)`\s*\|\s*(\d{3})\s*\|", section, re.M)
    }

    assert documented, "no error codes parsed from docs/17 §4 — the table's shape changed"
    assert documented == ERROR_CODES


@pytest.mark.parametrize("code", sorted(ERROR_CODES))
def test_every_registered_code_has_a_plausible_status(code: str) -> None:
    assert 400 <= ERROR_CODES[code] <= 599


# ── Security headers (SC64) ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("x-content-type-options", "nosniff"),
        ("x-frame-options", "DENY"),
        ("referrer-policy", "strict-origin-when-cross-origin"),
        ("cross-origin-opener-policy", "same-origin"),
    ],
)
def test_security_headers(header: str, value: str, client: TestClient) -> None:
    assert client.get("/health").headers[header] == value


def test_security_headers_are_present_on_error_responses(client: TestClient) -> None:
    """The path most likely to be missed, since the response is built deep
    inside an exception handler."""
    assert client.get("/boom").headers["x-frame-options"] == "DENY"


def test_hsts_is_absent_locally(client: TestClient) -> None:
    """A browser that saw HSTS from a local plain-HTTP deployment would pin
    `localhost` to HTTPS and refuse to load anything on it afterwards,
    including other projects."""
    assert "strict-transport-security" not in client.get("/health").headers


def test_hsts_is_present_in_production(make_settings: SettingsFactory) -> None:
    app = create_app(
        make_settings(
            environment="production",
            sandbox_runtime="runsc",
            otel_endpoint=HttpUrl("https://otel.example.test"),
            github_client_id="gh-client",
        )
    )
    with TestClient(app) as production_client:
        header = production_client.get("/health").headers["strict-transport-security"]
    assert header == "max-age=63072000; includeSubDomains; preload"
