"""FastAPI application.

`docs/05` §2. T1.5 adds request-id middleware, structured logging with
redaction, and the standard error envelope; this is the auth surface only.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI

from roottrace_api import __version__
from roottrace_api.auth.dependencies import CurrentUser, get_settings
from roottrace_api.settings import Settings

app = FastAPI(title="RootTrace AI", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness. Deliberately does not touch the database — a health check that
    fails when Postgres blips causes a restart storm rather than reporting one."""
    return {"status": "ok", "version": __version__}


@app.get("/health/ready")
def ready(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
    """Readiness. Reaching this at all means the boot invariants passed."""
    return {
        "status": "ready",
        "environment": settings.environment,
        "deployment_tier": settings.deployment_tier,
        "github_mode": settings.github_mode,
    }


@app.get("/v1/auth/providers")
def auth_providers(settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
    """Which login paths this deployment offers (`A3` §5.1).

    GitHub OAuth needs a registered app, which not every developer has on day
    one, so magic link is always available as a fallback. Both paths issue the
    same token with the same `sub`, so `rt_auth.uid()` and every RLS policy
    behave identically — a developer on magic link is exercising the real
    authorization path. **There is no dev-mode bypass**, which is why this
    endpoint reports what is configured rather than offering a way around it.
    """
    return {
        "github_oauth": settings.github_client_id is not None,
        "magic_link": True,
        "authorize_url": f"{str(settings.supabase_url).rstrip('/')}/auth/v1/authorize",
    }


@app.get("/v1/me")
def me(user: CurrentUser) -> dict[str, Any]:
    """The caller, as the database will see them.

    `user_id` is the value `rt_auth.uid()` resolves to, so this is the seam
    where the JWT and the RLS model must agree. The integration test asserts
    they do by reading tenant data with this same token.
    """
    return {"user_id": user.user_id, "email": user.email, "role": user.role}
