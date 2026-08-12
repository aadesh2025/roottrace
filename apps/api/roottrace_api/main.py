"""FastAPI application.

`docs/05` §2. A factory rather than a module-level `app`, so that the boot
invariants run when the process starts (uvicorn calls this with `--factory`)
rather than at import, and so a test can build an app around a specific
`Settings` without mutating a shared instance.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Depends, FastAPI

from roottrace_api import __version__
from roottrace_api.auth.dependencies import CurrentUser, get_settings
from roottrace_api.errors import register_error_handlers
from roottrace_api.log import configure_logging, get_logger
from roottrace_api.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from roottrace_api.settings import Settings

logger = get_logger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Passing `settings` is for tests. In a real process it is omitted, and
    `get_settings()` runs the boot invariants — a misconfigured deployment then
    fails to start rather than serving traffic in an unintended shape.
    """
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title="RootTrace AI",
        version=__version__,
        # CORS is deliberately absent until the dashboard origin exists (T8.2).
        # With no CORS middleware the browser default is that no cross-origin
        # page can read a response, which is the safe direction to be wrong in;
        # a permissive placeholder would not be.
    )

    # Every route resolves the same Settings the app was built with. In a real
    # process this is what `get_settings()` returns anyway; it matters when a
    # test builds an app around a specific configuration.
    resolved = settings
    app.dependency_overrides[get_settings] = lambda: resolved

    # Order matters and is not cosmetic. `add_middleware` prepends, so the last
    # one added is outermost. Security headers must be outermost so they also
    # land on the error envelope that RequestContextMiddleware produces for an
    # unhandled exception — which it sends through its own `send`, one layer
    # further in.
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)

    register_error_handlers(app)
    _register_routes(app)

    logger.info(
        "api_started",
        environment=settings.environment,
        deployment_tier=settings.deployment_tier,
        github_mode=settings.github_mode,
    )
    return app


def _register_routes(app: FastAPI) -> None:
    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness. Deliberately does not touch the database — a health check
        that fails when Postgres blips causes a restart storm rather than
        reporting one."""
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

        GitHub OAuth needs a registered app, which not every developer has on
        day one, so magic link is always available as a fallback. Both paths
        issue the same token with the same `sub`, so `rt_auth.uid()` and every
        RLS policy behave identically — a developer on magic link is exercising
        the real authorization path. **There is no dev-mode bypass**, which is
        why this endpoint reports what is configured rather than offering a way
        around it.
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
