"""Auth against real GoTrue, and the seam between the JWT and RLS.

`docs/15` T1.4. Nothing here mocks the identity provider: a token verified
against a stubbed key set proves our parser works, not that we can verify what
Supabase actually issues. That distinction mattered — local GoTrue signs
**ES256**, and a verifier pinned to RS256 (as `A3` §1 specifies) rejects every
real token while passing every unit test.

Refresh rotation and reuse detection are GoTrue's responsibility. We configure
and verify them; we do not reimplement a token store.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

pytestmark = [pytest.mark.integration, pytest.mark.security]

SUPABASE_URL = os.environ.get("RT_SUPABASE_URL", "http://127.0.0.1:54321")
PASSWORD = "correct-horse-battery-staple-REPLACE_ME"


@pytest.fixture(scope="module")
def admin_key() -> str:
    """GoTrue's admin key, from the environment and nowhere else.

    It had a hard-coded default here, and the gitleaks pre-commit hook caught
    it. Two ways to make that stop failing: allowlist this file, or stop
    committing the credential. The first is fail-open — the next real key
    pasted into this file would also be ignored — so `make test-integration`
    reads the key out of the running stack instead.

    The name is deliberately not `RT_`-prefixed. That namespace belongs to the
    Settings model, whose unrecognised-RT_* invariant refuses to boot on any
    variable in it without a matching field. It caught the first attempt at
    this fixture, which is the invariant working: it cannot tell a harness
    variable from an application one that was retired last year.
    """
    key = os.environ.get("ROOTTRACE_TEST_ADMIN_KEY")
    if not key:
        pytest.fail(
            "ROOTTRACE_TEST_ADMIN_KEY is unset. Run this suite with "
            "`make test-integration`, which reads the key from the running "
            "Supabase stack rather than storing it in the repository."
        )
    return key


@pytest.fixture(scope="module")
def gotrue() -> httpx.Client:
    return httpx.Client(base_url=f"{SUPABASE_URL}/auth/v1", timeout=10.0)


@pytest.fixture
def api() -> TestClient:
    """The real app, with the settings a local deployment boots with."""
    for key, value in {
        "RT_ENVIRONMENT": "ci",
        "RT_DEPLOYMENT_TIER": "evaluation",
        "RT_VERSION": "test",
        "RT_SERVICE_NAME": "api",
        "RT_DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:54322/postgres",
        "RT_SUPABASE_URL": SUPABASE_URL,
        "RT_SUPABASE_ANON_KEY": "anon-REPLACE_ME",
        "RT_SUPABASE_JWKS_URL": f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json",
    }.items():
        os.environ[key] = value

    from roottrace_api.auth import dependencies
    from roottrace_api.main import create_app

    dependencies.get_settings.cache_clear()
    dependencies.get_jwks.cache_clear()
    return TestClient(create_app())


@pytest.fixture
def signed_in_user(gotrue: httpx.Client, admin_key: str) -> dict[str, Any]:
    """A real user, signed in through GoTrue, holding a real access token."""
    email = f"t1-4-{uuid.uuid4().hex[:12]}@example.test"
    headers = {"apikey": admin_key, "Authorization": f"Bearer {admin_key}"}

    created = gotrue.post(
        "/admin/users",
        headers=headers,
        json={"email": email, "password": PASSWORD, "email_confirm": True},
    )
    assert created.status_code in (200, 201), created.text
    user_id = created.json()["id"]

    session = gotrue.post(
        "/token",
        params={"grant_type": "password"},
        headers={"apikey": admin_key},
        json={"email": email, "password": PASSWORD},
    )
    assert session.status_code == 200, session.text
    body = session.json()

    yield {"id": user_id, "email": email, **body}

    gotrue.delete(f"/admin/users/{user_id}", headers=headers)


def test_signed_in_user_is_returned_by_v1_me(
    api: TestClient, signed_in_user: dict[str, Any]
) -> None:
    """Sign in → JWT issued → /v1/me returns the user (T1.4 acceptance)."""
    response = api.get(
        "/v1/me", headers={"Authorization": f"Bearer {signed_in_user['access_token']}"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["user_id"] == signed_in_user["id"]
    assert response.json()["email"] == signed_in_user["email"]


def test_the_same_token_scopes_rls_correctly(signed_in_user: dict[str, Any]) -> None:
    """The seam. `/v1/me` agreeing is not enough — the value it reports has to
    be the value `rt_auth.uid()` resolves to, or the API and the database
    disagree about who is calling.

    Asserted by giving the database the token's own claims and reading a project
    the user was made a member of.
    """
    user_id = signed_in_user["id"]
    org_id, project_id = uuid.uuid4(), uuid.uuid4()

    with psycopg.connect(
        "postgresql://postgres:postgres@127.0.0.1:54322/postgres", autocommit=False
    ) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "insert into organizations (id, name, slug) values (%s,'O',%s)",
                (org_id, f"o-{org_id.hex[:8]}"),
            )
            cur.execute(
                "insert into projects (id, organization_id, name, slug) values (%s,%s,'P','p')",
                (project_id, org_id),
            )
            cur.execute(
                "insert into project_members (project_id, user_id, role) values (%s,%s,'owner')",
                (project_id, user_id),
            )
            # Another tenant, which this user must not see.
            other_org, other_project = uuid.uuid4(), uuid.uuid4()
            cur.execute(
                "insert into organizations (id, name, slug) values (%s,'X',%s)",
                (other_org, f"x-{other_org.hex[:8]}"),
            )
            cur.execute(
                "insert into projects (id, organization_id, name, slug) values (%s,%s,'X','x')",
                (other_project, other_org),
            )

            cur.execute("set local role authenticated")
            cur.execute(
                "select set_config('request.jwt.claims', %s, true)",
                (f'{{"sub":"{user_id}","role":"authenticated"}}',),
            )

            cur.execute("select rt_auth.uid()")
            resolved = cur.fetchone()
            assert resolved is not None
            assert str(resolved[0]) == user_id, "rt_auth.uid() disagrees with the JWT subject"

            cur.execute("select count(*) from projects where id = %s", (project_id,))
            mine = cur.fetchone()
            cur.execute("select count(*) from projects where id = %s", (other_project,))
            theirs = cur.fetchone()

        conn.rollback()

    assert mine is not None and mine[0] == 1, "the signed-in user cannot see their own project"
    assert theirs is not None and theirs[0] == 0, "the signed-in user saw another tenant"


def test_a_token_from_another_issuer_is_rejected(api: TestClient) -> None:
    """Cross-signed with a key this deployment has never seen."""
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    forged = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        key,
        algorithm="ES256",
        headers={"kid": str(uuid.uuid4())},
    )
    response = api.get("/v1/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_missing_and_malformed_authorization_are_rejected(api: TestClient) -> None:
    assert api.get("/v1/me").status_code == 401
    assert api.get("/v1/me", headers={"Authorization": "Basic abc"}).status_code == 401
    assert api.get("/v1/me", headers={"Authorization": "Bearer not-a-jwt"}).status_code == 401


def test_refresh_token_rotates(
    gotrue: httpx.Client, admin_key: str, signed_in_user: dict[str, Any]
) -> None:
    """Rotation, verified against GoTrue rather than reimplemented (A5).

    Asserts only what is actually true today: each refresh issues a NEW token.

    **Reuse detection is NOT verified, and that is a known gap, not an
    omission.** `docs/11` T15 and §3.1 require that presenting a consumed
    refresh token be rejected — it is not. Replaying one returns 200 with
    `GOTRUE_SECURITY_REFRESH_TOKEN_REUSE_INTERVAL` set to both 10 and 0
    (confirmed inside the container's environment), which suggests 0 means
    "unlimited" rather than "none" in this build. Setting it to 0 would
    therefore weaken the control while looking like it tightened it.

    Tracked as an open item on T1.4 in `docs/15`. Until it is resolved, a stolen
    refresh token is replayable, so this is a real gap in T15's mitigation and
    not a test-harness artefact.
    """
    original = signed_in_user["refresh_token"]

    first = gotrue.post(
        "/token",
        params={"grant_type": "refresh_token"},
        headers={"apikey": admin_key},
        json={"refresh_token": original},
    )
    assert first.status_code == 200, first.text
    assert first.json()["refresh_token"] != original, "GoTrue did not rotate the refresh token"
    assert first.json()["access_token"] != signed_in_user["access_token"]


def test_providers_endpoint_reports_magic_link_without_github(api: TestClient) -> None:
    """Missing OAuth credentials must not block anything (`A3` §5.1)."""
    body = api.get("/v1/auth/providers").json()
    assert body["magic_link"] is True
    assert body["github_oauth"] is False


def test_health_endpoints_need_no_auth(api: TestClient) -> None:
    assert api.get("/health").status_code == 200
    assert api.get("/health/ready").json()["deployment_tier"] == "evaluation"
