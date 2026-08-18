"""`TenantRepository` / `llm_calls` persistence (`11` §4 Layer 3, T5.1).

The actual `insert` against real Postgres is exercised by
`tests/integration/test_ai_db_persistence.py` — local Supabase is wedged on
this machine (`PROJECT-STATUS.md` §7), same limitation as every other
DB-backed suite in this repo. What is unit-testable without a connection is
the tenancy guard itself, and that it fires *before* the connection is ever
touched."""

from __future__ import annotations

import pytest

from roottrace_worker.ai.contracts import LLMCallRecord
from roottrace_worker.ai.db import LLMCallsRepository, TenancyViolation, TenantRepository

pytestmark = pytest.mark.unit


def _record(**overrides: object) -> LLMCallRecord:
    base: dict[str, object] = {
        "investigation_id": "inv_1",
        "project_id": "proj_1",
        "pipeline_step_id": "step_1",
        "stage": "reason",
        "tier": "reasoning-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "prompt_version": "v3",
        "prompt_url": "https://example/prompt.json",
        "response_url": "https://example/response.json",
        "prompt_hash": "abc123",
        "tokens_in": 100,
        "tokens_out": 50,
        "cost_micro_usd": 300,
        "latency_ms": 500,
        "attempt": 1,
    }
    base.update(overrides)
    return LLMCallRecord(**base)  # type: ignore[arg-type]


def test_require_project_id_rejects_none() -> None:
    with pytest.raises(TenancyViolation):
        TenantRepository._require_project_id(None)


def test_require_project_id_rejects_empty_string() -> None:
    with pytest.raises(TenancyViolation):
        TenantRepository._require_project_id("")


def test_require_project_id_accepts_a_real_id() -> None:
    assert TenantRepository._require_project_id("proj_1") == "proj_1"


class _ConnectionThatMustNotBeTouched:
    """If the tenancy guard fires *after* trying to use the connection
    rather than before, calling any attribute here fails the test loudly
    instead of silently doing nothing."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"connection.{name} was accessed despite a missing project_id")


async def test_insert_raises_before_touching_the_connection_when_project_id_is_missing() -> None:
    repo = LLMCallsRepository(_ConnectionThatMustNotBeTouched())  # type: ignore[arg-type]
    with pytest.raises(TenancyViolation):
        await repo.insert(_record(project_id=""))


class _FakeCursor:
    """Enough of `psycopg.AsyncCursor`'s contract for `insert` to run
    against — no real connection, but real coverage of the SQL/parameter
    construction, which the tenancy-only tests above never reach."""

    def __init__(self) -> None:
        self.executed: tuple[str, tuple[object, ...]] | None = None

    async def __aenter__(self) -> _FakeCursor:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.executed = (query, params)

    async def fetchone(self) -> tuple[str]:
        return ("row_generated_id",)


class _FakeConnection:
    def __init__(self) -> None:
        self.last_cursor: _FakeCursor | None = None

    def cursor(self) -> _FakeCursor:
        self.last_cursor = _FakeCursor()
        return self.last_cursor


async def test_insert_builds_the_expected_sql_and_returns_the_new_id() -> None:
    conn = _FakeConnection()
    repo = LLMCallsRepository(conn)  # type: ignore[arg-type]

    row_id = await repo.insert(_record(tier="reasoning-a"))

    assert row_id == "row_generated_id"
    assert conn.last_cursor is not None
    query, params = conn.last_cursor.executed  # type: ignore[misc]
    assert "insert into llm_calls" in query
    # tier converts hyphen to underscore for the DB enum (params[4], after
    # investigation_id, project_id, pipeline_step_id, stage).
    assert params[4] == "reasoning_a"
    assert params[1] == "proj_1"


def test_tier_db_value_converts_the_hyphen_to_an_underscore() -> None:
    """`04` §8's `llm_tier` enum spells `reasoning_a`; `Tier` spells
    `reasoning-a`. This is the one conversion point — see
    `contracts.py`'s module docstring."""
    record = _record(tier="reasoning-a")
    assert record.tier_db_value == "reasoning_a"


def test_tier_db_value_is_unchanged_for_single_word_tiers() -> None:
    assert _record(tier="fast").tier_db_value == "fast"
