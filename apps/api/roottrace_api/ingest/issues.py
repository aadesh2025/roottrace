"""The issue upsert (`03` §S2).

> The upsert is a single atomic statement — this is the hot path during a
> storm.

One statement, not read-then-write. During a 10,000-occurrence storm every
worker is upserting the same fingerprint at the same moment; a `SELECT` followed
by an `INSERT or UPDATE` produces duplicate issues and a wrong
`occurrence_count`, and both failures are silent. `ON CONFLICT … DO UPDATE`
collapses the decision into one round trip that Postgres serialises for us.

`(xmax = 0)` is how the statement reports which branch it took: zero means the
row was inserted by this statement, non-zero means it was updated. There is no
other way to know, and a second query to find out would reintroduce the race
the upsert exists to remove.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover — import-time only
    from psycopg import AsyncConnection

_UPSERT = """
insert into issues (project_id, fingerprint, error_type, normalized_message,
                    sample_message, culprit, route_pattern,
                    first_seen, last_seen, occurrence_count, status,
                    environments, affected_releases)
values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1, 'open',
        array[%s]::environment_kind[], array[%s]::text[])
on conflict (project_id, fingerprint) do update
  set last_seen         = greatest(issues.last_seen, excluded.last_seen),
      occurrence_count  = issues.occurrence_count + 1,
      -- A resolved issue that recurs is a regression, and saying so is the
      -- whole point of tracking status: it is the difference between "known"
      -- and "we thought we fixed this".
      status            = case when issues.status = 'resolved' then 'regressed'
                               else issues.status end,
      is_regression     = issues.status = 'resolved' or issues.is_regression,
      environments      = (
        select array_agg(distinct e)
          from unnest(issues.environments || excluded.environments) as e
      ),
      affected_releases = (
        select array_agg(distinct r)
          from unnest(issues.affected_releases || excluded.affected_releases) as r
         where r is not null
      )
returning id::text, occurrence_count, first_seen, last_seen,
          (xmax = 0) as is_new, status::text, is_regression
"""


@dataclass(frozen=True, slots=True)
class IssueUpsert:
    issue_id: str
    occurrence_count: int
    first_seen: datetime
    last_seen: datetime
    is_new_issue: bool
    status: str
    is_regression: bool


async def upsert_issue(
    conn: AsyncConnection,
    *,
    project_id: str,
    fingerprint: str,
    error_type: str,
    normalized_message: str,
    sample_message: str,
    culprit: str | None,
    route_pattern: str | None,
    seen_at: datetime,
    environment: str,
    release: str | None,
) -> IssueUpsert:
    """Record one occurrence, creating the issue if this is the first."""
    async with conn.cursor() as cur:
        await cur.execute(
            _UPSERT,
            (
                project_id,
                fingerprint,
                error_type,
                # Stored truncated: this column is indexed and displayed, and an
                # 8 KB message in a list view helps nobody.
                normalized_message[:2000],
                sample_message[:2000],
                culprit,
                route_pattern,
                seen_at,
                seen_at,
                environment,
                release,
            ),
        )
        row: Any = await cur.fetchone()

    return IssueUpsert(
        issue_id=row[0],
        occurrence_count=int(row[1]),
        first_seen=row[2],
        last_seen=row[3],
        is_new_issue=bool(row[4]),
        status=row[5],
        is_regression=bool(row[6]),
    )
