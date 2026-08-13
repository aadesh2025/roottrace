"""The issue upsert under concurrency (T2.3, `03` §S2).

T2.3's acceptance: *100 concurrent identical inserts produce exactly one issue
with `occurrence_count = 100`.*

Run as a real race against real Postgres. This is the hot path during a storm,
and the failure a read-then-write formulation produces — duplicate issues and a
wrong count — appears only under concurrency and is silent when it does.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import psycopg
import psycopg_pool
import pytest

from roottrace_api.ingest.issues import upsert_issue

pytestmark = [pytest.mark.integration, pytest.mark.security]

DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


@pytest.fixture
def project_id() -> Iterator[str]:
    org_id, project = uuid.uuid4(), uuid.uuid4()
    slug = f"fp-{org_id.hex[:8]}"
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


async def _pool() -> AsyncIterator[psycopg_pool.AsyncConnectionPool]:
    pool = psycopg_pool.AsyncConnectionPool(DSN, min_size=4, max_size=20, open=False)
    await pool.open()
    try:
        yield pool
    finally:
        await pool.close()


async def _record(
    pool: psycopg_pool.AsyncConnectionPool, project: str, fingerprint: str, **overrides: object
) -> object:
    async with pool.connection() as conn, conn.transaction():
        return await upsert_issue(
            conn,
            project_id=project,
            fingerprint=fingerprint,
            error_type=str(overrides.get("error_type", "TypeError")),
            normalized_message="unsupported operand type(s) for +",
            sample_message="unsupported operand type(s) for +",
            culprit="services/checkout.py::calculate_total",
            route_pattern="/api/v2/checkout",
            seen_at=overrides.get("seen_at") or datetime.now(UTC),  # type: ignore[arg-type]
            environment=str(overrides.get("environment", "production")),
            release=overrides.get("release", "v2.14.3"),  # type: ignore[arg-type]
        )


def _issue_rows(project: str) -> list[tuple]:
    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            # `environments` is an array of a custom enum, which psycopg has no
            # loader for and would hand back as an unparsed string — sorting
            # that compares characters, not values.
            "select fingerprint, occurrence_count, status, is_regression, "
            "environments::text[], affected_releases from issues where project_id = %s",
            (project,),
        )
        return list(cur.fetchall())


# ── The acceptance criterion ───────────────────────────────────────────────


def test_100_concurrent_identical_inserts_produce_one_issue(project_id: str) -> None:
    """T2.3 verbatim.

    A read-then-write upsert passes every sequential test and produces
    duplicate issues here — the count would be short by however many workers
    raced, and nothing downstream would report it.
    """
    fingerprint = uuid.uuid4().hex[:32]

    async def storm() -> list[object]:
        agen = _pool()
        pool = await anext(agen)
        try:
            return list(
                await asyncio.gather(*(_record(pool, project_id, fingerprint) for _ in range(100)))
            )
        finally:
            await agen.aclose()

    results = asyncio.run(storm())

    rows = _issue_rows(project_id)
    assert len(rows) == 1, f"expected one issue, got {len(rows)}"
    assert rows[0][1] == 100, f"occurrence_count is {rows[0][1]}, not 100"

    # Exactly one of the hundred created it. `(xmax = 0)` is how the statement
    # reports which branch it took; a second query to find out would reopen the
    # race the upsert exists to close.
    assert sum(1 for result in results if result.is_new_issue) == 1  # type: ignore[attr-defined]


def test_different_fingerprints_stay_different_issues(project_id: str) -> None:
    """The other direction. An upsert that merged everything would pass the
    test above and destroy the product."""

    async def mixed() -> None:
        agen = _pool()
        pool = await anext(agen)
        try:
            await asyncio.gather(
                *(_record(pool, project_id, uuid.uuid4().hex[:32]) for _ in range(10))
            )
        finally:
            await agen.aclose()

    asyncio.run(mixed())
    assert len(_issue_rows(project_id)) == 10


def test_the_same_fingerprint_in_another_project_is_another_issue(project_id: str) -> None:
    """Fingerprints are only unique within a project. Two tenants running the
    same framework produce identical hashes for their own bugs."""
    fingerprint = uuid.uuid4().hex[:32]
    other_org, other_project = uuid.uuid4(), uuid.uuid4()
    slug = f"fp2-{other_org.hex[:8]}"

    with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            "insert into organizations (id, name, slug) values (%s,%s,%s)", (other_org, slug, slug)
        )
        cur.execute(
            "insert into projects (id, organization_id, name, slug) values (%s,%s,%s,%s)",
            (other_project, other_org, slug, slug),
        )

    async def both() -> None:
        agen = _pool()
        pool = await anext(agen)
        try:
            await _record(pool, project_id, fingerprint)
            await _record(pool, str(other_project), fingerprint)
        finally:
            await agen.aclose()

    try:
        asyncio.run(both())
        assert len(_issue_rows(project_id)) == 1
        assert len(_issue_rows(str(other_project))) == 1
    finally:
        with psycopg.connect(DSN, autocommit=True) as conn, conn.cursor() as cur:
            cur.execute("delete from organizations where id = %s", (other_org,))


# ── What the upsert maintains ──────────────────────────────────────────────


def test_first_seen_is_preserved_and_last_seen_advances(project_id: str) -> None:
    fingerprint = uuid.uuid4().hex[:32]
    early = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    late = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)

    async def twice() -> tuple:
        agen = _pool()
        pool = await anext(agen)
        try:
            first = await _record(pool, project_id, fingerprint, seen_at=early)
            second = await _record(pool, project_id, fingerprint, seen_at=late)
            return first, second
        finally:
            await agen.aclose()

    first, second = asyncio.run(twice())

    assert first.is_new_issue and not second.is_new_issue  # type: ignore[attr-defined]
    assert second.first_seen == early  # type: ignore[attr-defined]
    assert second.last_seen == late  # type: ignore[attr-defined]


def test_an_out_of_order_occurrence_does_not_rewind_last_seen(project_id: str) -> None:
    """Events arrive late — a buffered SDK flushing after a network partition.
    Letting one rewind `last_seen` would make an active issue look dormant."""
    fingerprint = uuid.uuid4().hex[:32]
    late = datetime(2026, 8, 4, 9, 0, tzinfo=UTC)
    early = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)

    async def out_of_order() -> object:
        agen = _pool()
        pool = await anext(agen)
        try:
            await _record(pool, project_id, fingerprint, seen_at=late)
            return await _record(pool, project_id, fingerprint, seen_at=early)
        finally:
            await agen.aclose()

    result = asyncio.run(out_of_order())
    assert result.last_seen == late  # type: ignore[attr-defined]


def test_a_resolved_issue_that_recurs_is_a_regression(project_id: str) -> None:
    """`03` §S2. The difference between "known" and "we thought we fixed
    this" is the whole reason status is tracked."""
    fingerprint = uuid.uuid4().hex[:32]

    async def resolve_then_recur() -> object:
        agen = _pool()
        pool = await anext(agen)
        try:
            await _record(pool, project_id, fingerprint)
            async with pool.connection() as conn, conn.transaction(), conn.cursor() as cur:
                await cur.execute(
                    "update issues set status = 'resolved' where project_id = %s",
                    (project_id,),
                )
            return await _record(pool, project_id, fingerprint)
        finally:
            await agen.aclose()

    result = asyncio.run(resolve_then_recur())

    assert result.status == "regressed"  # type: ignore[attr-defined]
    assert result.is_regression  # type: ignore[attr-defined]


def test_environments_and_releases_accumulate_without_duplicating(project_id: str) -> None:
    """One bug seen in staging and production is one issue that names both."""
    fingerprint = uuid.uuid4().hex[:32]

    async def across() -> None:
        agen = _pool()
        pool = await anext(agen)
        try:
            await _record(
                pool, project_id, fingerprint, environment="production", release="v2.14.3"
            )
            await _record(pool, project_id, fingerprint, environment="staging", release="v2.14.2")
            await _record(
                pool, project_id, fingerprint, environment="production", release="v2.14.3"
            )
        finally:
            await agen.aclose()

    asyncio.run(across())
    row = _issue_rows(project_id)[0]

    assert sorted(row[4]) == ["production", "staging"]
    assert sorted(row[5]) == ["v2.14.2", "v2.14.3"]
    assert row[1] == 3
