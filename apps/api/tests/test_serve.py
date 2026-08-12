"""The process entrypoint (T1.5).

Small surface, but every value in it was a defect when it was wrong: uvicorn's
default log config replaced the processor chain, and its access log duplicated
every request line without a request id. Pinned here so a later edit that looks
harmless cannot quietly undo either.
"""

from __future__ import annotations

from typing import Any

import pytest
import uvicorn

from roottrace_api import serve
from roottrace_api.auth.dependencies import get_settings

pytestmark = pytest.mark.unit

ENVIRONMENT = {
    "RT_ENVIRONMENT": "local",
    "RT_VERSION": "test",
    "RT_SERVICE_NAME": "api",
    "RT_DATABASE_URL": "postgresql://postgres:postgres@localhost:54322/postgres",
    "RT_SUPABASE_URL": "http://localhost:54321",
    "RT_SUPABASE_ANON_KEY": "anon-REPLACE_ME",
    "RT_SUPABASE_JWKS_URL": "http://localhost:54321/auth/v1/.well-known/jwks.json",
}


@pytest.fixture
def uvicorn_call(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    for key, value in ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()

    captured: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        captured["app"] = app
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    serve.main()
    get_settings.cache_clear()
    return captured


def test_uvicorn_does_not_replace_our_logging(uvicorn_call: dict[str, Any]) -> None:
    """`log_config=None`. Anything else installs uvicorn's plain-text handler
    on the root logger, which un-redacts and un-structures every line."""
    assert uvicorn_call["log_config"] is None


def test_uvicorns_access_log_is_off(uvicorn_call: dict[str, Any]) -> None:
    """Our middleware emits the request line, carrying the request id and the
    duration that uvicorn's does not. Both on means two lines per request."""
    assert uvicorn_call["access_log"] is False


def test_the_app_is_built_by_the_factory(uvicorn_call: dict[str, Any]) -> None:
    """Factory mode is what makes the boot invariants run before the port
    opens, so a misconfigured deployment never accepts a request."""
    assert uvicorn_call["factory"] is True
    assert uvicorn_call["app"] == "roottrace_api.main:create_app"


def test_settings_are_resolved_before_the_server_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A misconfiguration must fail here, not after the socket is listening and
    a load balancer has marked the instance healthy."""
    for key, value in ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("RT_SUPABASE_SERVICE_ROLE_KEY", "service-role-REPLACE_ME")
    get_settings.cache_clear()

    def fail_if_called(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("uvicorn started despite a failed boot invariant")

    monkeypatch.setattr(uvicorn, "run", fail_if_called)
    with pytest.raises(ValueError, match="must not hold the service-role key"):
        serve.main()

    get_settings.cache_clear()
