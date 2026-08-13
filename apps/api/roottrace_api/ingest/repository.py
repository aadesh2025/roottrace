"""Persisting a batch (`03` §S1 steps 1 and 7).

Every statement here runs as `rt_ingest` with `rt.project_id` set from the
verified key, so the database pins each row to the authenticated project. The
handler cannot write across tenants even if it computes the wrong id — see
`20260801001600_ingest_role.sql`.

Both are `SET LOCAL`: they end with the transaction. On a pooled connection a
role or a GUC that outlived its transaction would scope the *next* tenant's
request to the previous one's project, which is the worst failure this design
could have.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING

from roottrace_api.ingest.events import ValidatedEvent
from roottrace_api.ingest.keys import ResolvedKey, matches

if TYPE_CHECKING:  # pragma: no cover — import-time only
    from psycopg import AsyncConnection

#: One statement for the whole batch. `03` §S1 step 7 says "batch, one
#: statement", and the p95 budget is 50 ms for 100 events — a round trip per
#: event would spend it all on latency.
_INSERT = """
insert into raw_events
    (project_id, api_key_id, batch_id, event_ts, environment,
     service, release, payload, payload_bytes, redactions)
select %s, %s, %s,
       unnest(%s::timestamptz[]),
       unnest(%s::environment_kind[]),
       unnest(%s::text[]),
       unnest(%s::text[]),
       unnest(%s::jsonb[]),
       unnest(%s::integer[]),
       unnest(%s::jsonb[])
"""


async def scope_to_project(conn: AsyncConnection, project_id: str) -> None:
    """Drop into the ingest role for the rest of this transaction."""
    async with conn.cursor() as cur:
        await cur.execute("select set_config('rt.project_id', %s, true)", (project_id,))
        await cur.execute("set local role rt_ingest")


async def resolve_key(conn: AsyncConnection, key_hash: str) -> ResolvedKey | None:
    """Look up an API key by hash.

    Runs *before* `scope_to_project`, since the project is what this returns.
    The comparison is redone in Python with a constant-time compare: the SQL
    equality is what makes the index usable, and the second check is what makes
    the decision not depend on the database's comparison semantics.
    """
    async with conn.cursor() as cur:
        await cur.execute(
            """
            select id::text, project_id::text, scopes, key_hash
              from api_keys
             where key_hash = %s
               and revoked_at is null
             limit 1
            """,
            (key_hash,),
        )
        row = await cur.fetchone()

    if row is None or not matches(str(row[3]), key_hash):
        return None
    return ResolvedKey(key_id=row[0], project_id=row[1], scopes=tuple(row[2] or ()))


async def insert_events(
    conn: AsyncConnection,
    *,
    project_id: str,
    api_key_id: str,
    batch_id: uuid.UUID,
    events: list[ValidatedEvent],
    redactions: list[list[dict[str, str]]] | None = None,
) -> int:
    """Insert every accepted event. Returns the number written."""
    if not events:
        return 0

    async with conn.cursor() as cur:
        await cur.execute(
            _INSERT,
            (
                project_id,
                api_key_id,
                str(batch_id),
                [event.event_ts for event in events],
                [event.environment for event in events],
                [event.service for event in events],
                [event.release for event in events],
                [json.dumps(event.payload, separators=(",", ":")) for event in events],
                [event.payload_bytes for event in events],
                [
                    json.dumps(item, separators=(",", ":"))
                    for item in (redactions or [[] for _ in events])
                ],
            ),
        )
        # rowcount is not available under pipeline mode until the results are
        # fetched, and the caller does not need it: a failed insert raises.
        return len(events)
