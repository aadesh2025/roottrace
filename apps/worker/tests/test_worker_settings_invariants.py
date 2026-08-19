"""Boot invariants for the worker's `Settings` (`A3` §1, T5.1).

Same discipline as `apps/api/tests/test_settings_invariants.py`: each case
asserts a misconfiguration cannot start, not that it merely looks wrong.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from roottrace_worker.settings import Settings

pytestmark = pytest.mark.unit

BASE: dict[str, Any] = {
    "environment": "local",
    "database_url": "postgresql://postgres:postgres@localhost:54322/postgres",
    "supabase_url": "http://localhost:54321",
    "supabase_service_role_key": "service-role-REPLACE_ME",
    "anthropic_api_key": "sk-ant-REPLACE_ME",
}


def settings(**overrides: Any) -> Settings:
    return Settings(**{**BASE, **overrides})


def test_baseline_configuration_boots() -> None:
    """Positive control. Every rejection below is meaningless if a correct
    configuration cannot start either."""
    assert settings().llm_config_path == "infra/config/models.yaml"


def test_openai_key_alone_is_also_sufficient() -> None:
    without_anthropic: dict[str, Any] = {k: v for k, v in BASE.items() if k != "anthropic_api_key"}
    without_anthropic["openai_api_key"] = "sk-REPLACE_ME"
    assert Settings(**without_anthropic) is not None


def test_no_provider_key_at_all_refuses_to_boot() -> None:
    """Every tier in `models.yaml` leads with anthropic or openai — a
    deployment with neither key configured cannot serve a single call, and
    should fail at boot, not at the first pipeline run that needs one."""
    without_anthropic = {k: v for k, v in BASE.items() if k != "anthropic_api_key"}
    with pytest.raises(ValidationError, match="RT_ANTHROPIC_API_KEY"):
        Settings(**without_anthropic)


def test_defaults_match_a3s_documented_values() -> None:
    result = settings()
    assert result.llm_timeout_seconds == 60
    assert result.llm_max_retries == 3
    assert result.llm_cache_ttl_seconds == 3600
    assert result.default_daily_cost_cap_micro_usd == 5_000_000
    assert result.default_monthly_cost_cap_micro_usd == 100_000_000
    assert result.cost_reservation_estimate_micro_usd == 420_000


def test_sandbox_defaults_match_a3s_documented_values() -> None:
    result = settings()
    assert result.sandbox_enabled is True
    assert result.sandbox_runtime == "runsc"
    assert result.sandbox_concurrency == 4
    assert result.sandbox_timeout_seconds == 90
    assert result.sandbox_target_p95_seconds == 45
    assert result.sandbox_memory_limit_mb == 512
    assert result.sandbox_cpu_limit == 1.0
    assert result.sandbox_pids_limit == 128
    assert result.sandbox_disk_limit_mb == 256
    assert result.sandbox_max_stdout_bytes == 524_288
    assert result.sandbox_image_python == "roottrace/sandbox-python:3.12"
    assert result.sandbox_image_node == "roottrace/sandbox-node:20"
    assert result.sandbox_reaper_interval_seconds == 60
    assert result.sandbox_orphan_max_age_seconds == 120
    assert result.sandbox_apparmor_profile is None


def test_environment_is_required() -> None:
    without_environment = {k: v for k, v in BASE.items() if k != "environment"}
    with pytest.raises(ValidationError, match="environment"):
        Settings(**without_environment)


def test_production_requires_gvisor() -> None:
    with pytest.raises(ValidationError, match="gVisor required in production"):
        settings(environment="production", sandbox_runtime="runc")


def test_production_requires_sandbox_enabled() -> None:
    with pytest.raises(ValidationError, match="sandbox cannot be disabled"):
        settings(environment="production", sandbox_enabled=False)


def test_production_with_runsc_and_sandbox_enabled_boots() -> None:
    result = settings(environment="production", sandbox_runtime="runsc")
    assert result.environment == "production"


def test_non_production_may_use_runc() -> None:
    """`A3` §5's own local-dev example sets `RT_SANDBOX_RUNTIME=runc` —
    gVisor is commonly unavailable on a developer machine or in CI."""
    result = settings(environment="local", sandbox_runtime="runc")
    assert result.sandbox_runtime == "runc"
    result = settings(environment="ci", sandbox_runtime="runc")
    assert result.sandbox_runtime == "runc"
