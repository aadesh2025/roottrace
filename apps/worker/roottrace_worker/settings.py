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
from typing import Literal, Self

from pydantic import HttpUrl, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Duplicated from `apps/api/roottrace_api/settings.py` rather than shared —
#: same "separate deployables, no cross-dependency" reasoning as the rest of
#: this file. Only `Environment` is needed here: the worker is where
#: `sandbox_runtime` is actually consumed to launch a container, so its own
#: boot invariant (`A3` §6: "gVisor required in production") belongs here,
#: not just in `api`'s copy, which only displays the setting.
Environment = Literal["local", "ci", "staging", "production"]
SandboxRuntime = Literal["runc", "runsc"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RT_",
        extra="forbid",
        case_sensitive=False,
        env_file=None,
    )

    # ── Core ────────────────────────────────────────────────────────────────
    #: Required, no default — same forcing function as `api`'s copy of this
    #: field (`A3` §6): a production worker that forgot to set this must fail
    #: to boot, not silently run at `local` rigour with gVisor unenforced.
    environment: Environment

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

    # ── Sandbox (T6.1-T6.3, `07`, `A3` §"Sandbox") ──────────────────────────
    #: `runsc` is the documented default (`A3`'s own table) — the boot
    #: invariant below requires it in production. Local/CI overrides to
    #: `runc` (`A3` §5's own local-dev example: "gVisor often unavailable
    #: locally") when gVisor isn't installed on the host; `07` §11 treats this
    #: as an accepted, disclosed operational gap, not a relaxed control —
    #: L1/L2/L3/L7 (network, filesystem, identity, secrets) hold regardless of
    #: which runtime is selected. Seccomp filtering on `runc` relies on
    #: Docker's own built-in default profile rather than a hand-authored one
    #: (see `pipeline/validate/orchestrator.py`'s module docstring for why).
    sandbox_enabled: bool = True
    sandbox_runtime: SandboxRuntime = "runsc"
    sandbox_concurrency: int = 4
    sandbox_timeout_seconds: int = 90
    sandbox_target_p95_seconds: int = 45
    sandbox_memory_limit_mb: int = 512
    sandbox_cpu_limit: float = 1.0
    sandbox_pids_limit: int = 128
    sandbox_disk_limit_mb: int = 256
    sandbox_max_stdout_bytes: int = 524_288
    sandbox_image_python: str = "roottrace/sandbox-python:3.12"
    sandbox_image_node: str = "roottrace/sandbox-node:20"
    sandbox_reaper_interval_seconds: int = 60
    sandbox_orphan_max_age_seconds: int = 120
    #: Not in `A3`'s documented env var table — added here, and to `A3` in
    #: the same commit (`CLAUDE.md`: doc drift is a defect). `None` means
    #: "apply no override," which leaves Docker's own automatic
    #: `docker-default` AppArmor confinement active rather than nothing at
    #: all (same reasoning as the seccomp default, see the module note
    #: above). A custom `roottrace-sandbox` profile (`07` §3 L3) is host-
    #: provisioned infrastructure no ticket through T6.3 builds or loads;
    #: this field exists so a deployment that *has* loaded one can name it.
    sandbox_apparmor_profile: str | None = None

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

        # ── Sandbox: operational rigour, `A3` §6 ────────────────────────────
        if self.environment == "production":
            if self.sandbox_runtime != "runsc":
                raise ValueError("gVisor required in production")
            if not self.sandbox_enabled:
                raise ValueError("sandbox cannot be disabled in production")
        return self


def load_settings() -> Settings:
    """A thin wrapper so callers do not construct `Settings()` directly —
    matches `apps/api/roottrace_api/settings.py`'s own boot pattern, and
    gives every future assertion this file grows exactly one call site to
    add it to."""
    return Settings()  # type: ignore[call-arg]  # fields resolve from RT_* env vars
