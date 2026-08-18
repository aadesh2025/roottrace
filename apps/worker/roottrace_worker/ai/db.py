"""`llm_calls` persistence (`04` §8) via `TenantRepository` (`11` §4 Layer 3,
`CLAUDE.md` non-negotiable).

**The worker runs as `service_role` and therefore bypasses RLS** — `11` §4
is explicit that this makes a repository base class, not Postgres, the only
thing standing between a query and a cross-tenant read or write. Every
method here that touches `llm_calls` takes `project_id` explicitly and
raises `TenancyViolation` if it is missing, rather than defaulting to
"whatever the caller happened to pass" the way an ORM's implicit session
scoping would.

Raw parameterized SQL via `psycopg`, matching `apps/api/roottrace_api/
ingest/repository.py`'s style — `11` §4's own sketch is ORM-flavoured
pseudocode, but nothing in this codebase uses an ORM, and the *principle*
(no query without an explicit `project_id`) does not depend on the query
being built one way or the other."""

from __future__ import annotations

from typing import TYPE_CHECKING

from roottrace_worker.ai.contracts import LLMCallRecord

if TYPE_CHECKING:  # pragma: no cover — import-time only
    from psycopg import AsyncConnection


class TenancyViolation(Exception):
    """`11` §4 Layer 3, raised rather than silently scoping to nothing —
    the base every tenant-table repository in this service must extend."""


class TenantRepository:
    """`project_id` is required, not merely accepted, on every subclass
    method that reaches a tenant table. `_require_project_id` is the one
    place that rule is enforced, so a repository method that forgets to
    call it is the only way this protection can be bypassed — and that
    omission is a one-line code-review catch, not a query with a silently
    wrong scope."""

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn

    @staticmethod
    def _require_project_id(project_id: str | None) -> str:
        if not project_id:
            raise TenancyViolation("query requires an explicit project_id")
        return project_id


_INSERT_LLM_CALL = """
insert into llm_calls
    (investigation_id, project_id, pipeline_step_id, stage, tier, provider,
     model, prompt_version, prompt_url, response_url, prompt_hash,
     tokens_in, tokens_out, cached_tokens_in, cost_micro_usd, latency_ms,
     attempt, failover_from, schema_repair_used, suspicious_content_detected)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
returning id::text
"""


class LLMCallsRepository(TenantRepository):
    async def insert(self, record: LLMCallRecord) -> str:
        """Returns the new row's id. `record.project_id` is what
        `_require_project_id` checks — a record built without one is a
        programming error the type system does not catch (the field is a
        plain `str`, not `str | None`, precisely so a caller cannot
        construct one without a value), so this is the belt to that
        braces."""
        project_id = self._require_project_id(record.project_id)
        async with self._conn.cursor() as cur:
            await cur.execute(
                _INSERT_LLM_CALL,
                (
                    record.investigation_id,
                    project_id,
                    record.pipeline_step_id,
                    record.stage,
                    record.tier_db_value,
                    record.provider,
                    record.model,
                    record.prompt_version,
                    record.prompt_url,
                    record.response_url,
                    record.prompt_hash,
                    record.tokens_in,
                    record.tokens_out,
                    record.cached_tokens_in,
                    record.cost_micro_usd,
                    record.latency_ms,
                    record.attempt,
                    record.failover_from,
                    record.schema_repair_used,
                    record.suspicious_content_detected,
                ),
            )
            row = await cur.fetchone()
        if row is None:  # pragma: no cover — `insert ... returning` always yields one row
            raise RuntimeError("llm_calls insert returned no row")
        return str(row[0])
