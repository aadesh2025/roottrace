"""Shared fixtures for the `api` unit suite."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import HttpUrl, SecretStr

from roottrace_api.settings import Settings

LOCAL_SUPABASE = "http://localhost:54321"

SettingsFactory = Callable[..., Settings]


def api_settings(**overrides: Any) -> Settings:
    """A minimal valid `api` configuration.

    Values are constructed with their real types rather than strings coerced by
    pydantic, so `mypy --strict` checks these call sites the same way it checks
    production ones. A test helper that silences the type checker hides exactly
    the mistakes it should catch.
    """
    base: dict[str, Any] = {
        "environment": "local",
        "version": "test",
        "service_name": "api",
        "database_url": "postgresql://postgres:postgres@localhost:54322/postgres",
        "supabase_url": HttpUrl(LOCAL_SUPABASE),
        "supabase_anon_key": SecretStr("anon-REPLACE_ME"),
        "supabase_jwks_url": HttpUrl(f"{LOCAL_SUPABASE}/auth/v1/.well-known/jwks.json"),
    }
    return Settings(**{**base, **overrides})


@pytest.fixture
def make_settings() -> SettingsFactory:
    """The factory, as a fixture.

    Injected rather than imported: `apps/api/tests` is not a package, so a
    cross-module import here would depend on pytest's sys.path insertion.
    """
    return api_settings
