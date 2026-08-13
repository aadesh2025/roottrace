"""The ingest write path is scoped by the database (T2.1).

`rt_ingest` exists because three rules collide: S1 runs in `api`, `api` must not
hold the service-role key, and `raw_events` has forced RLS with no INSERT grant
to `authenticated`.

The property under test is that **the handler cannot write across tenants even
if it tries**. Application-layer scoping is a check that can be got wrong by a
mixed-up loop variable; a `WITH CHECK` cannot. So every test below writes as
`rt_ingest` with a deliberately wrong `rt.project_id` and asserts the database
refuses it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.security]

DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


@pytest.fixture
def conn() -> Iterator[psycopg.Connection]:
    """A connection rolled back at the end of every test."""
    with psycopg.connect(DSN, autocommit=False) as connection:
        yield connection
        connection.rollback()


def _project(cur: psycopg.Cursor, slug: str) -> uuid.UUID:
    org_id, project_id = uuid.uuid4(), uuid.uuid4()
    cur.execute(
        "insert into organizations (id, name, slug) values (%s,%s,%s)", (org_id, slug, slug)
    )
    cur.execute(
        "insert into projects (id, organization_id, name, slug) values (%s,%s,%s,%s)",
        (project_id, org_id, slug, slug),
    )
    return project_id


def _as_ingest(cur: psycopg.Cursor, project_id: uuid.UUID | None) -> None:
    """Become the ingest role, scoped to a project.

    `SET LOCAL ROLE` rather than a second database credential: there is no
    password to store, rotate or leak, and the privilege drop is what makes RLS
    apply — a superuser connection bypasses it entirely.
    """
    cur.execute("select set_config('rt.project_id', %s, true)", (str(project_id or ""),))
    cur.execute("set local role rt_ingest")


def _insert(cur: psycopg.Cursor, project_id: uuid.UUID) -> None:
    cur.execute(
        """
        insert into raw_events
            (project_id, batch_id, event_ts, environment, payload, payload_bytes)
        values (%s, %s, now(), 'production', '{}'::jsonb, 2)
        """,
        (project_id, uuid.uuid4()),
    )


# ── The control ────────────────────────────────────────────────────────────


def test_ingest_can_write_to_its_own_project(conn: psycopg.Connection) -> None:
    """The positive control. Every refusal below means nothing if the
    supported path does not work either."""
    with conn.cursor() as cur:
        project_id = _project(cur, f"own-{uuid.uuid4().hex[:8]}")
        _as_ingest(cur, project_id)
        _insert(cur, project_id)

        cur.execute("reset role")
        cur.execute("select count(*) from raw_events where project_id = %s", (project_id,))
        row = cur.fetchone()
    assert row is not None and row[0] == 1


# ── Cross-tenant writes are refused by the database ────────────────────────


def test_ingest_cannot_write_to_another_project(conn: psycopg.Connection) -> None:
    """The whole reason the role exists.

    The handler here is doing the worst thing it could: authenticated as one
    project and inserting a row labelled with another. The `WITH CHECK` is what
    stops it, so no application bug can produce a cross-tenant write.
    """
    with conn.cursor() as cur:
        mine = _project(cur, f"mine-{uuid.uuid4().hex[:8]}")
        theirs = _project(cur, f"theirs-{uuid.uuid4().hex[:8]}")
        _as_ingest(cur, mine)

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _insert(cur, theirs)


def test_ingest_cannot_write_without_a_project_scope(conn: psycopg.Connection) -> None:
    """An unset GUC must fail closed. If `current_project()` returned something
    permissive when unset, forgetting to set it would open every write."""
    with conn.cursor() as cur:
        project_id = _project(cur, f"unset-{uuid.uuid4().hex[:8]}")
        _as_ingest(cur, None)

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _insert(cur, project_id)


def test_the_scope_cannot_outlive_its_transaction(conn: psycopg.Connection) -> None:
    """`set_config(..., true)` is local. A GUC that leaked across a pooled
    connection would scope the next tenant's request to the previous one's
    project — the worst possible failure in a pooled service."""
    with conn.cursor() as cur:
        project_id = _project(cur, f"local-{uuid.uuid4().hex[:8]}")
        _as_ingest(cur, project_id)
    conn.rollback()

    with conn.cursor() as cur:
        cur.execute("select rt_auth.current_project()")
        row = cur.fetchone()
    assert row is not None and row[0] is None


# ── Partitions (B13, again) ────────────────────────────────────────────────


def test_ingest_can_write_through_a_partition_directly(conn: psycopg.Connection) -> None:
    """Policies and grants do not propagate to partitions, and an INSERT routed
    to one is checked against that partition's own policies. Asserted against
    the partition by name, because writing through the parent would not prove
    the partition carries the policy."""
    with conn.cursor() as cur:
        project_id = _project(cur, f"part-{uuid.uuid4().hex[:8]}")
        # This month's partition, since the row below is dated now(). Picking
        # an arbitrary one would fail the partition constraint and look like an
        # RLS pass for the wrong reason.
        cur.execute("select format('raw_events_%s', to_char(now(), 'YYYY_MM'))")
        row = cur.fetchone()
        assert row is not None
        partition = row[0]

        _as_ingest(cur, project_id)
        # S608 is suppressed because the relation name comes from now(), not
        # from anything a caller supplies.
        cur.execute(
            f"insert into {partition} "  # noqa: S608
            "(project_id, batch_id, event_ts, environment, payload, payload_bytes) "
            "values (%s, %s, now(), 'production', '{}'::jsonb, 2)",
            (project_id, uuid.uuid4()),
        )


def test_every_raw_events_partition_carries_the_ingest_policy(conn: psycopg.Connection) -> None:
    """The maintenance job creates next month's partition. If the policy lived
    only in this migration, ingest would start failing when the month rolled
    over — the worst possible failure signature."""
    with conn.cursor() as cur:
        cur.execute(
            """
            select c.relname
              from pg_class c
              join pg_inherits i on i.inhrelid = c.oid
              join pg_class p on p.oid = i.inhparent
             where p.relname = 'raw_events'
               and not exists (
                     select 1 from pg_policies
                      where schemaname = 'public'
                        and tablename = c.relname
                        and policyname = 'ingest_write')
            """
        )
        missing = [row[0] for row in cur.fetchall()]
    assert missing == [], f"partitions without the ingest policy: {missing}"


def test_a_newly_created_partition_is_writable_by_ingest(conn: psycopg.Connection) -> None:
    """Runs the maintenance job and checks its output, rather than trusting
    that the function was edited correctly."""
    with conn.cursor() as cur:
        cur.execute("select rt_admin.ensure_partitions(6, 2)")
        cur.execute(
            """
            select count(*)
              from pg_class c
              join pg_inherits i on i.inhrelid = c.oid
              join pg_class p on p.oid = i.inhparent
             where p.relname = 'raw_events'
               and not exists (
                     select 1 from pg_policies
                      where schemaname = 'public'
                        and tablename = c.relname
                        and policyname = 'ingest_write')
            """
        )
        row = cur.fetchone()
    assert row is not None and row[0] == 0


# ── The role holds nothing else ────────────────────────────────────────────


def test_ingest_cannot_read_raw_events(conn: psycopg.Connection) -> None:
    """Write-only, like the API key that maps to it. A leaked ingest key cannot
    expose a single stored event (`11` §3)."""
    with conn.cursor() as cur:
        project_id = _project(cur, f"read-{uuid.uuid4().hex[:8]}")
        _as_ingest(cur, project_id)

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("select * from raw_events limit 1")


@pytest.mark.parametrize(
    "table",
    ["investigations", "patches", "root_cause_analyses", "audit_log", "usage_daily"],
)
def test_ingest_cannot_read_the_rest_of_the_pipeline(conn: psycopg.Connection, table: str) -> None:
    """The read/write separation is the most important authentication decision
    in the product (`11` §3), and it has to be true of the database role and
    not only of the API key."""
    with conn.cursor() as cur:
        project_id = _project(cur, f"deny-{uuid.uuid4().hex[:8]}")
        _as_ingest(cur, project_id)

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            # S608: the table name is a literal from the parametrize list.
            cur.execute(f"select * from {table} limit 1")  # noqa: S608


def test_ingest_cannot_write_anything_but_raw_events(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        project_id = _project(cur, f"write-{uuid.uuid4().hex[:8]}")
        _as_ingest(cur, project_id)

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute("insert into issues (project_id) values (%s)", (project_id,))


def test_ingest_sees_only_its_own_project_row(conn: psycopg.Connection) -> None:
    """It needs one project row to confirm the key's project exists. It does
    not need anyone else's."""
    with conn.cursor() as cur:
        mine = _project(cur, f"pmine-{uuid.uuid4().hex[:8]}")
        theirs = _project(cur, f"pother-{uuid.uuid4().hex[:8]}")
        _as_ingest(cur, mine)

        cur.execute("select count(*) from projects where id = %s", (mine,))
        own = cur.fetchone()
        cur.execute("select count(*) from projects where id = %s", (theirs,))
        other = cur.fetchone()

    assert own is not None and own[0] == 1
    assert other is not None and other[0] == 0


def test_ingest_holds_no_bypassrls(conn: psycopg.Connection) -> None:
    """ADR-009 removed the only privileged role in the system. Adding one back
    for ingest would undo that decision quietly."""
    with conn.cursor() as cur:
        cur.execute("select rolbypassrls, rolcanlogin from pg_roles where rolname = 'rt_ingest'")
        row = cur.fetchone()

    assert row is not None, "rt_ingest does not exist"
    assert row[0] is False, "rt_ingest holds BYPASSRLS"
    assert row[1] is False, "rt_ingest can log in; it is reached by SET LOCAL ROLE"
