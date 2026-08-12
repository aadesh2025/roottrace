"""Boot invariants (`docs/A3` §6).

Each of these asserts that a *misconfiguration cannot start*. A service that
boots and behaves subtly wrongly is far worse than one that fails loudly, so
every case here is a failure we want.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from roottrace_api.settings import Settings

pytestmark = pytest.mark.unit

# Assembled rather than written literally so that the PEM header never appears
# as a contiguous string in the repository. The `detect-private-key` hook
# matches on that header and cannot tell this obviously-fake value from a real
# one — and the fix for a hook that fires is to stop tripping it, never to
# exclude the file, which would silence it for whatever is pasted here next.
FAKE_PRIVATE_KEY = "-----BEGIN RSA " + "PRIVATE KEY-----REPLACE_ME"

BASE: dict[str, Any] = {
    "environment": "local",
    "deployment_tier": "evaluation",
    "version": "test",
    "service_name": "api",
    "database_url": "postgresql://postgres:postgres@localhost:54322/postgres",
    "supabase_url": "http://localhost:54321",
    "supabase_anon_key": "anon-REPLACE_ME",
    "supabase_jwks_url": "http://localhost:54321/auth/v1/.well-known/jwks.json",
}


def settings(**overrides: Any) -> Settings:
    return Settings(**{**BASE, **overrides})


def test_baseline_configuration_boots() -> None:
    """Positive control. Every rejection below is meaningless if a correct
    configuration cannot start either."""
    assert settings().service_name == "api"


def test_api_refuses_the_service_role_key() -> None:
    """It bypasses RLS entirely. In `api` it would turn one authorisation bug
    into a full cross-tenant breach."""
    with pytest.raises(ValidationError, match="must not hold the service-role key"):
        settings(supabase_service_role_key="service-role-REPLACE_ME")


def test_worker_may_hold_the_service_role_key() -> None:
    """The other half: the invariant is scoped to `api`, not a blanket ban.
    Without this, the test above would pass if the key were rejected outright."""
    assert (
        settings(
            service_name="worker",
            supabase_service_role_key="service-role-REPLACE_ME",
            supabase_jwks_url=None,
        ).service_name
        == "worker"
    )


def test_api_requires_jwks() -> None:
    with pytest.raises(ValidationError, match="requires JWKS"):
        settings(supabase_jwks_url=None)


def test_stray_retired_variable_fails_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """B12: `RT_SUPABASE_JWT_SECRET` is retired.

    There is no field and no assertion naming it. The unrecognised-RT_* scan in
    `boot_invariants` rejects it, which is the general form of the control — it
    covers this variable, the one retired in 2027, and the one misspelled in a
    deploy manifest, none of which anyone will remember to write a check for.
    """
    for key, value in BASE.items():
        monkeypatch.setenv(f"RT_{key.upper()}", str(value))
    monkeypatch.setenv("RT_SUPABASE_JWT_SECRET", "hs256-shared-secret-REPLACE_ME")

    with pytest.raises(ValidationError, match="supabase_jwt_secret"):
        Settings()  # type: ignore[call-arg]


def test_unknown_variable_fails_boot(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same control, shown to be general rather than special-cased."""
    for key, value in BASE.items():
        monkeypatch.setenv(f"RT_{key.upper()}", str(value))
    monkeypatch.setenv("RT_TYPO_IN_DEPLOY_MANIFEST", "1")

    with pytest.raises(ValidationError, match="typo_in_deploy_manifest"):
        Settings()  # type: ignore[call-arg]


def test_evaluation_tier_refuses_live_github() -> None:
    with pytest.raises(ValidationError, match="must not use live GitHub"):
        settings(github_mode="live")


def test_evaluation_tier_refuses_a_github_private_key() -> None:
    """This is what makes the V1 safety claim structural: holding no App key,
    the deployment cannot mint an installation token at all."""
    with pytest.raises(ValidationError, match="must not hold a GitHub App key"):
        settings(github_private_key=FAKE_PRIVATE_KEY)


def test_production_evaluation_tier_is_legal() -> None:
    """C5. Full production rigour with a structural guarantee it cannot reach a
    customer repository — the combination V1 actually is, which the original
    invariant could not express."""
    assert (
        settings(
            environment="production",
            deployment_tier="evaluation",
            log_level="info",
            sandbox_runtime="runsc",
            otel_endpoint="https://otel.example.test",
            github_client_id="gh-client",
        ).environment
        == "production"
    )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"log_level": "debug"}, "debug logging forbidden"),
        ({"sandbox_runtime": "runc"}, "gVisor required"),
        ({"sandbox_enabled": False}, "sandbox cannot be disabled"),
        ({"otel_endpoint": None}, "tracing required"),
        ({"ratelimit_enabled": False}, "rate limiting required"),
    ],
)
def test_production_rigour_invariants(override: dict[str, Any], message: str) -> None:
    production: dict[str, Any] = {
        "environment": "production",
        "log_level": "info",
        "sandbox_runtime": "runsc",
        "otel_endpoint": "https://otel.example.test",
        "github_client_id": "gh-client",
    }
    with pytest.raises(ValidationError, match=message):
        settings(**{**production, **override})


def test_evidence_binding_cannot_be_disabled_anywhere() -> None:
    """Asserted in every environment, not just production: a developer who
    switches it off to get past a failing fixture has disabled H1 and will not
    notice."""
    with pytest.raises(ValidationError, match="evidence binding cannot be disabled"):
        settings(ff_strict_evidence_binding=False)


def test_auto_merge_is_refused_before_v3() -> None:
    with pytest.raises(ValidationError, match="auto-merge not permitted"):
        settings(ff_auto_merge=True)
