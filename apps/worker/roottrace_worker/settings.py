"""Typed settings for the worker (`A3` §1, §"LLM").

**Deliberately duplicated from `apps/api/roottrace_api/settings.py`, not
shared.** The two packages have never depended on each other — separate
deployables, separate privilege boundaries (`CLAUDE.md`: the worker runs as
`service_role` and holds keys `api` must never hold). Same tradeoff the SDK
already made for the same reason (`PROJECT-STATUS.md` §4). This file covers
only what T5.1 needs — the fields `apps/api/roottrace_api/settings.py`
declares for the API surface (GitHub OAuth, sandbox toggles the API reads
for display, etc.) have no reason to exist here."""

from __future__ import annotations

import os
from typing import Self

from pydantic import HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RT_",
        extra="forbid",
        case_sensitive=False,
        env_file=None,
    )

    # ── Database / queue ────────────────────────────────────────────────────
    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    # ── Supabase ────────────────────────────────────────────────────────────
    supabase_url: HttpUrl
    #: Worker only (`11` §5.1) — bypasses RLS entirely, which is exactly why
    #: `apps/api/roottrace_api/settings.py`'s own boot invariant forbids this
    #: field's equivalent from ever being set there.
    supabase_service_role_key: SecretStr
    #: `A3` §"Supabase". Default matches the doc's documented default —
    #: one shared bucket, path-prefixed per artifact type (`03` §S1 step 8's
    #: `raw/...` precedent; `ai/storage.py`'s own `PATH_PREFIX` for this one).
    storage_bucket: str = "roottrace-artifacts"

    # ── LLM (T5.1, `06` §2.2, `A3` §"LLM") ──────────────────────────────────
    llm_config_path: str = "infra/config/models.yaml"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    voyage_api_key: SecretStr | None = None
    llm_timeout_seconds: int = 60
    llm_max_retries: int = 3
    llm_cache_ttl_seconds: int = 3600
    llm_enable_prompt_caching: bool = True

    # ── Pipeline / cost (`A3` §"Rate limits and quotas") ────────────────────
    pipeline_max_repair_attempts: int = 3
    default_daily_cost_cap_micro_usd: int = 5_000_000
    default_monthly_cost_cap_micro_usd: int = 100_000_000
    cost_reservation_enabled: bool = True
    cost_reservation_estimate_micro_usd: int = 420_000

    @model_validator(mode="after")
    def boot_invariants(self) -> Self:
        """A narrower version of `apps/api/roottrace_api/settings.py`'s same
        method — this service only needs to assert what T5.1 actually
        depends on existing at boot, not the full `api` invariant set,
        which covers concerns (GitHub App tiering, sandbox runtime, GitHub
        OAuth) this file has no fields for. The unknown-`RT_*`-variable scan
        below is kept, though, not narrowed — the worker holds *more*
        sensitive credentials than `api` (the service-role key, provider
        keys), so a typo'd or retired env var being silently ignored here
        is a worse failure mode here than there, not a lesser one."""
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

        if self.anthropic_api_key is None and self.openai_api_key is None:
            raise ValueError(
                "at least one of RT_ANTHROPIC_API_KEY / RT_OPENAI_API_KEY must be set — "
                "every configured tier in models.yaml leads with one of these two providers"
            )
        return self


def load_settings() -> Settings:
    """A thin wrapper so callers do not construct `Settings()` directly —
    matches `apps/api/roottrace_api/settings.py`'s own boot pattern, and
    gives every future assertion this file grows exactly one call site to
    add it to."""
    return Settings()  # type: ignore[call-arg]  # fields resolve from RT_* env vars
