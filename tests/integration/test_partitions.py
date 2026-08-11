"""Partition security and partition coverage — B13.

`docs/04` §12.10, `docs/14` §4.1. Partitions are separate relations that inherit
neither RLS nor policies nor grants, so every property has to be asserted on the
partition itself rather than on the parent.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from tests.conftest import Tenant, as_postgres, as_user

pytestmark = [pytest.mark.integration, pytest.mark.security]

PARTITIONED_PARENTS = ("raw_events", "error_occurrences")


def _partition_for(conn: psycopg.Connection[Any], parent: str, when: datetime) -> str:
    return f"{parent}_{when:%Y_%m}"


def _insert_occurrence(
    conn: psycopg.Connection[Any], tenant: Tenant, occurred_at: datetime
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """insert into error_occurrences (project_id, issue_id, raw_event_id, occurred_at,
                                              environment)
               values (%s,%s,gen_random_uuid(),%s,'production')""",
            (tenant.project_id, tenant.issue_id, occurred_at),
        )


@pytest.mark.parametrize("parent", PARTITIONED_PARENTS)
def test_every_partition_has_forced_rls_and_policies(
    conn: psycopg.Connection[Any], parent: str
) -> None:
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute(
            """select c.relname, c.relrowsecurity, c.relforcerowsecurity,
                      (select count(*) from pg_policy p where p.polrelid = c.oid),
                      has_table_privilege('authenticated', c.oid, 'SELECT')
                 from pg_class c
                 join pg_inherits i on i.inhrelid = c.oid
                where i.inhparent = %s::regclass""",
            (parent,),
        )
        rows = cur.fetchall()

    assert rows, f"{parent} has no partitions at all"
    for name, rls, forced, policies, can_select in rows:
        assert rls and forced, f"{name}: RLS not enabled and forced"
        assert policies >= 2, f"{name}: expected the parent's 2 policies, found {policies}"
        # Without the grant a direct read fails with `permission denied`, which
        # resembles isolation without being it — and would make the isolation
        # test below pass for the wrong reason.
        assert can_select, f"{name}: authenticated has no SELECT, so isolation is untestable"


@pytest.mark.parametrize("parent", PARTITIONED_PARENTS)
def test_direct_partition_access_is_scoped(
    conn: psycopg.Connection[Any], tenants: tuple[Tenant, Tenant], parent: str
) -> None:
    """The hole B13 describes: naming a partition directly consults only that
    partition's policies. The parent's are never reached."""
    a, b = tenants
    partition = _partition_for(conn, parent, datetime.now(UTC))
    as_user(conn, a.user_id)
    with conn.cursor() as cur:
        cur.execute(
            f"select count(*) from {partition} where project_id = %s",  # noqa: S608
            (a.project_id,),
        )
        own = cur.fetchone()
        cur.execute(
            f"select count(*) from {partition} where project_id = %s",  # noqa: S608
            (b.project_id,),
        )
        other = cur.fetchone()

    assert own is not None and other is not None
    assert own[0] >= 1, f"{partition}: positive control failed — own rows not visible"
    assert other[0] == 0, f"{partition}: LEAKED tenant B's rows via direct partition access"


def test_late_arriving_occurrence_lands_in_the_previous_partition(
    conn: psycopg.Connection[Any], tenants: tuple[Tenant, Tenant]
) -> None:
    """`p_months_behind` — the failure that would only appear on the 1st-7th.

    `occurred_at` is the CUSTOMER's timestamp and S1 accepts events up to 7 days
    old (RT-INGEST-0012), so an event arriving on the 2nd legitimately carries
    last month's date. Without a partition behind the current month that insert
    fails with `no partition of relation found for row`: intermittent, one week
    a month, and indistinguishable from a bad payload.

    The clock is pinned by construction rather than by mocking: rather than wait
    for the 2nd, this dates the row six days before the START of the current
    month, which is the same relative position without a fake clock.
    """
    a, _ = tenants
    as_postgres(conn)
    now = datetime.now(UTC)
    this_month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    six_days_before_the_2nd = this_month_start + timedelta(days=1) - timedelta(days=6)

    _insert_occurrence(conn, a, six_days_before_the_2nd)

    expected = _partition_for(conn, "error_occurrences", six_days_before_the_2nd)
    with conn.cursor() as cur:
        cur.execute(
            "select tableoid::regclass::text from error_occurrences where occurred_at = %s",
            (six_days_before_the_2nd,),
        )
        row = cur.fetchone()
    assert row is not None, "the late-arriving occurrence was not inserted"
    assert row[0] == expected, f"landed in {row[0]}, expected {expected}"


def test_out_of_range_date_fails_loudly(
    conn: psycopg.Connection[Any], tenants: tuple[Tenant, Tenant]
) -> None:
    """No DEFAULT partition. A DEFAULT would absorb this silently, and detaching
    one later is painful — that trades a loud failure for a quiet one."""
    a, _ = tenants
    as_postgres(conn)
    with pytest.raises(psycopg.errors.CheckViolation, match="no partition of relation"):
        _insert_occurrence(conn, a, datetime(2020, 1, 1, tzinfo=UTC))


def test_no_default_partition_exists(conn: psycopg.Connection[Any]) -> None:
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute(
            """select count(*) from pg_class c join pg_inherits i on i.inhrelid = c.oid
                where pg_get_expr(c.relpartbound, c.oid) = 'DEFAULT'"""
        )
        row = cur.fetchone()
    assert row is not None and row[0] == 0, "a DEFAULT partition exists; rows can land silently"


def test_ensure_partitions_secures_everything_it_creates(conn: psycopg.Connection[Any]) -> None:
    """The monthly job is where B13 would regress: it runs unattended, creates a
    relation, and nobody reviews a code change because there isn't one."""
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute("select rt_admin.ensure_partitions(6, 2)")
        cur.execute(
            """select c.relname
                 from pg_class c
                 join pg_inherits i on i.inhrelid = c.oid
                where i.inhparent in ('raw_events'::regclass, 'error_occurrences'::regclass)
                  and (not c.relrowsecurity
                       or not c.relforcerowsecurity
                       or not exists (select 1 from pg_policy p where p.polrelid = c.oid)
                       or not has_table_privilege('authenticated', c.oid, 'SELECT'))"""
        )
        unsecured = [r[0] for r in cur.fetchall()]
    assert unsecured == [], f"ensure_partitions created unsecured partitions: {unsecured}"


def test_an_unsecured_partition_would_be_caught(conn: psycopg.Connection[Any]) -> None:
    """Teeth for the assertion migration.

    Creates a partition WITHOUT secure_partition() and runs the same query the
    …001500 assertion runs. If this does not flag it, the assertion cannot
    either, and the whole B13 defence is decorative.
    """
    as_postgres(conn)
    rogue = f"raw_events_rogue_{uuid.uuid4().hex[:8]}"
    with conn.cursor() as cur:
        cur.execute(
            f"""create table {rogue} partition of raw_events
                for values from ('2031-01-01') to ('2031-02-01')"""
        )
        cur.execute(
            """select count(*) from pg_class c join pg_namespace n on n.oid = c.relnamespace
                where n.nspname = 'public' and c.relkind in ('r','p')
                  and (not c.relrowsecurity or not c.relforcerowsecurity)"""
        )
        row = cur.fetchone()
    assert row is not None and row[0] >= 1, (
        "An unsecured partition did not trip the coverage assertion's own query. "
        "The migration-level guard would not catch it either."
    )
