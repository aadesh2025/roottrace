"""Typed settings and the boot invariants.

`docs/appendix/A3` §1 and §6. **The process refuses to start on missing or
invalid values.** A misconfigured service that starts and behaves subtly wrongly
is far worse than one that fails loudly.
"""

from __future__ import annotations

import os
from typing import Literal, Self

from pydantic import HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["local", "ci", "staging", "production"]
DeploymentTier = Literal["evaluation", "live"]
GithubMode = Literal["fixture", "replay", "live"]
ServiceName = Literal["api", "worker"]
SandboxRuntime = Literal["runc", "runsc"]
LogLevel = Literal["debug", "info", "warning", "error"]


class Settings(BaseSettings):
    """Every `RT_*` variable this service understands.

    Any `RT_`-prefixed variable that is not a field below fails the boot. That
    is a security control, not tidiness: it covers retired variables without a
    field and an assertion per retirement — most immediately
    `RT_SUPABASE_JWT_SECRET`, which B12 removed because it is a *signing* key,
    and its presence in `api` would let a compromised process mint valid tokens
    for any user. A public key cannot. It also covers the variable someone
    retires in 2027 and the one misspelled in a deploy manifest, neither of
    which anyone will remember to write an assertion for.

    ``extra="forbid"`` below is necessary but **not sufficient** for that, and
    the difference is easy to miss: pydantic-settings only collects environment
    variables matching a declared field, so `extra` never sees an unknown one.
    The explicit scan in `boot_invariants` is what actually enforces it.
    """

    model_config = SettingsConfigDict(
        env_prefix="RT_",
        extra="forbid",
        case_sensitive=False,
        env_file=None,
    )

    # ── Core ────────────────────────────────────────────────────────────────
    environment: Environment
    deployment_tier: DeploymentTier = "evaluation"
    version: str
    log_level: LogLevel = "info"
    service_name: ServiceName

    # ── Database / queue ────────────────────────────────────────────────────
    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    # ── Supabase ────────────────────────────────────────────────────────────
    supabase_url: HttpUrl
    supabase_anon_key: SecretStr
    # Worker only. Bypasses RLS entirely; see the invariant below.
    supabase_service_role_key: SecretStr | None = None
    supabase_jwks_url: HttpUrl | None = None
    supabase_jwks_cache_ttl_seconds: int = 86400

    # ── GitHub ──────────────────────────────────────────────────────────────
    github_mode: GithubMode = "fixture"
    github_app_id: int | None = None
    github_private_key: SecretStr | None = None
    github_webhook_secret: SecretStr | None = None
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    github_fixture_path: str = "fixtures/synthetic-repo"

    # ── Sandbox ─────────────────────────────────────────────────────────────
    sandbox_enabled: bool = True
    sandbox_runtime: SandboxRuntime = "runsc"
    sandbox_timeout_seconds: int = 90
    sandbox_target_p95_seconds: int = 45

    # ── Observability ───────────────────────────────────────────────────────
    otel_endpoint: HttpUrl | None = None

    # ── Rate limiting ───────────────────────────────────────────────────────
    ratelimit_enabled: bool = True

    # ── Feature flags ───────────────────────────────────────────────────────
    ff_strict_evidence_binding: bool = True
    ff_auto_merge: bool = False

    @model_validator(mode="after")
    def boot_invariants(self) -> Self:
        """`A3` §6. Every failure here is deliberate and load-bearing."""

        # ── Unrecognised RT_* variables ─────────────────────────────────────
        # `extra="forbid"` on the model is not sufficient by itself:
        # pydantic-settings only collects environment variables that match a
        # declared field, so an unknown RT_* var is silently ignored rather than
        # rejected. The scan below is what actually enforces it.
        #
        # This is deliberately general rather than one assertion per retired
        # variable. It covers RT_SUPABASE_JWT_SECRET (B12 — a *signing* key,
        # whose presence in `api` would let a compromised process mint tokens
        # for any user), the variable retired in 2027, and the one misspelled in
        # a deploy manifest. Nobody writes an assertion for the last two.
        known = {f"rt_{name}" for name in type(self).model_fields}
        unknown = sorted(
            name.lower()
            for name in os.environ
            if name.lower().startswith("rt_") and name.lower() not in known
        )
        if unknown:
            raise ValueError(
                "unrecognised RT_* environment variable(s): "
                + ", ".join(u.removeprefix("rt_") for u in unknown)
                + " — retired or misspelled variables must not be silently ignored"
            )

        # ── Tier: what this deployment may touch (C5) ───────────────────────
        if self.deployment_tier == "evaluation":
            if self.github_mode not in ("fixture", "replay"):
                raise ValueError("SECURITY: evaluation tier must not use live GitHub")
            # A V1 deployment does not merely CHOOSE not to write to customer
            # repositories — holding no App key, it cannot mint an installation
            # token, so it structurally cannot. That is what makes the V1 safety
            # claim verifiable rather than aspirational (ADR-007).
            if self.github_private_key is not None:
                raise ValueError("SECURITY: evaluation tier must not hold a GitHub App key")
        else:
            if self.github_mode != "live":
                raise ValueError("live tier requires live GitHub")
            if self.github_app_id is None or self.github_private_key is None:
                raise ValueError("live tier requires a GitHub App id and private key")
            if self.github_webhook_secret is None:
                raise ValueError("live tier requires a webhook secret")

        # ── Environment: operational rigour ─────────────────────────────────
        if self.environment == "production":
            if self.log_level == "debug":
                raise ValueError("debug logging forbidden in production")
            if self.sandbox_runtime != "runsc":
                raise ValueError("gVisor required in production")
            if not self.sandbox_enabled:
                raise ValueError("sandbox cannot be disabled in production")
            if self.otel_endpoint is None:
                raise ValueError("tracing required in production")
            if not self.ratelimit_enabled:
                raise ValueError("rate limiting required in production")

        if self.environment in ("staging", "production") and self.github_client_id is None:
            raise ValueError("GitHub OAuth login required outside local/ci")

        # ── Always, in every environment and tier ───────────────────────────
        # Asserted everywhere, not just production: a developer who disables
        # evidence binding to get past a failing fixture has switched off the
        # primary anti-hallucination control (H1) and will not notice.
        if not self.ff_strict_evidence_binding:
            raise ValueError("evidence binding cannot be disabled")
        if self.ff_auto_merge:
            raise ValueError("auto-merge not permitted before V3")

        if self.service_name == "api":
            # That key bypasses RLS entirely. It belongs in the worker, which
            # legitimately processes many tenants, and nowhere else. An `api`
            # process holding it would turn one application-layer authorisation
            # bug into a full cross-tenant breach.
            if self.supabase_service_role_key is not None:
                raise ValueError("SECURITY: api service must not hold the service-role key")
            if self.supabase_jwks_url is None:
                raise ValueError("api requires JWKS for asymmetric token verification")

        return self
