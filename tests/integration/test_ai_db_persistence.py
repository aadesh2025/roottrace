"""`LLMCallsRepository.insert` against real Postgres (`04` §8, T5.1).

Local Supabase is wedged on Windows (`PROJECT-STATUS.md` §7) — this needs
the same `net stop winnat && net start winnat` fix as every other DB-backed
suite in this repo to run locally; CI is authoritative for it, same as the
rest of the integration suite.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from roottrace_worker.ai.contracts import LLMCallRecord
from roottrace_worker.ai.db import LLMCallsRepository, TenancyViolation

pytestmark = pytest.mark.integration

DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


@pytest.fixture
def project_id() -> Iterator[str]:
    org_id, project = uuid.uuid4(), uuid.uuid4()
    slug = f"llmcalls-{org_id.hex[:8]}"
    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "insert into organizations (id, name, slug) values (%s,%s,%s)", (org_id, slug, slug)
        )
        cur.execute(
            "insert into projects (id, organization_id, name, slug) values (%s,%s,%s,%s)",
            (project, org_id, slug, slug),
        )
    yield str(project)
    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("delete from organizations where id = %s", (org_id,))


def _record(project_id: str, **overrides: object) -> LLMCallRecord:
    base: dict[str, object] = {
        "investigation_id": None,
        "project_id": project_id,
        "pipeline_step_id": None,
        "stage": "reason",
        "tier": "reasoning-a",
        "provider": "anthropic",
        "model": "claude-sonnet-5",
        "prompt_version": "v3",
        "prompt_url": "https://example/prompt.json",
        "response_url": "https://example/response.json",
        "prompt_hash": "abc123",
        "tokens_in": 19_000,
        "tokens_out": 2_100,
        "cost_micro_usd": 88_500,
        "latency_ms": 4_200,
        "attempt": 1,
    }
    base.update(overrides)
    return LLMCallRecord(**base)  # type: ignore[arg-type]


async def test_insert_persists_a_row_readable_back_with_the_right_values(project_id: str) -> None:
    async with await psycopg.AsyncConnection.connect(DSN) as conn:
        repo = LLMCallsRepository(conn)
        row_id = await repo.insert(_record(project_id))
        await conn.commit()

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "select project_id::text, stage::text, tier::text, provider, model, "
            "tokens_in, tokens_out, cost_micro_usd, attempt "
            "from llm_calls where id = %s",
            (row_id,),
        )
        row = cur.fetchone()

    assert row is not None
    assert row[0] == project_id
    assert row[1] == "reason"
    # `Tier`'s hyphenated spelling converts to the DB enum's underscore form.
    assert row[2] == "reasoning_a"
    assert row[3] == "anthropic"
    assert row[4] == "claude-sonnet-5"
    assert row[5] == 19_000
    assert row[6] == 2_100
    assert row[7] == 88_500
    assert row[8] == 1


async def test_insert_without_a_project_id_raises_before_any_query(project_id: str) -> None:
    async with await psycopg.AsyncConnection.connect(DSN) as conn:
        repo = LLMCallsRepository(conn)
        with pytest.raises(TenancyViolation):
            await repo.insert(_record(project_id=""))


async def test_cost_is_stored_as_a_bigint_not_a_float(project_id: str) -> None:
    """`CLAUDE.md`: money and tokens are integers, never floats — this
    confirms the column itself, not just the Python type, enforces it."""
    async with await psycopg.AsyncConnection.connect(DSN) as conn:
        repo = LLMCallsRepository(conn)
        row_id = await repo.insert(_record(project_id, cost_micro_usd=140_000))
        await conn.commit()

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "select pg_typeof(cost_micro_usd)::text from llm_calls where id = %s", (row_id,)
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == "bigint"
