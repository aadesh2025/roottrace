"""Cross-tenant isolation across all 26 tenant tables.

`docs/14` §4.1. Three cases per table, and the first one is not optional:

  * **positive control** — user A CAN see their own rows
  * negative — user A sees ZERO of user B's rows
  * write — a `viewer` cannot mutate anything

Without the positive control the negative test is vacuous. A missing GRANT
returns zero rows just as convincingly as a working policy, and the whole suite
would stay green with every policy dropped. That is not hypothetical: during
T1.2 all three migration-level assertions passed while every policy in the
database raised `permission denied` at query time, and only the positive control
found it.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from tests.conftest import SCOPE_COLUMN, TENANT_TABLES, Tenant, as_postgres, as_user

pytestmark = [pytest.mark.integration, pytest.mark.security]


def _count(conn: psycopg.Connection[Any], table: str, column: str, value: uuid.UUID) -> int:
    with conn.cursor() as cur:
        cur.execute(f"select count(*) from {table} where {column} = %s", (value,))  # noqa: S608
        row = cur.fetchone()
        assert row is not None
        return int(row[0])


def _scope(table: str, tenant: Tenant) -> tuple[str, uuid.UUID]:
    column = SCOPE_COLUMN.get(table, "project_id")
    if table == "organizations":
        return column, tenant.org_id
    if column == "organization_id":
        return column, tenant.org_id
    if table == "projects":
        return column, tenant.project_id
    return column, tenant.project_id


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_own_rows_are_visible(
    conn: psycopg.Connection[Any], tenants: tuple[Tenant, Tenant], table: str
) -> None:
    """POSITIVE CONTROL. Everything below is meaningless if this does not hold."""
    a, _ = tenants
    column, value = _scope(table, a)
    as_user(conn, a.user_id)
    assert _count(conn, table, column, value) >= 1, (
        f"{table}: user A cannot see their OWN rows. The isolation test for this "
        f"table would now pass for the wrong reason — check GRANTs before policies."
    )


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_rls_blocks_cross_tenant_read(
    conn: psycopg.Connection[Any], tenants: tuple[Tenant, Tenant], table: str
) -> None:
    a, b = tenants
    column, value = _scope(table, b)
    as_user(conn, a.user_id)
    assert _count(conn, table, column, value) == 0, f"RLS FAILURE: {table} leaked across tenants"


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_rls_blocks_cross_tenant_write(
    conn: psycopg.Connection[Any], tenants: tuple[Tenant, Tenant], table: str
) -> None:
    """An UPDATE aimed at another tenant's rows must affect nothing.

    Expressed as an UPDATE rather than an INSERT so it applies uniformly: every
    table has rows to aim at, and `with check` and `using` are both exercised.
    """
    a, b = tenants
    column, value = _scope(table, b)
    as_user(conn, a.user_id)
    with conn.cursor() as cur:
        try:
            # Self-assignment of the scope column: a no-op write that every one
            # of the 26 tables supports, so no table needs skipping. `usage_daily`
            # has no created_at, and a skipped test is not a passing one.
            cur.execute(
                f"update {table} set {column} = {column} where {column} = %s",  # noqa: S608
                (value,),
            )
        except psycopg.errors.InsufficientPrivilege:
            return  # No write grant at all is a stricter pass than a filtered one.
        assert cur.rowcount == 0, f"RLS FAILURE: {table} allowed a cross-tenant write"


def test_viewer_can_read_but_cannot_write(
    conn: psycopg.Connection[Any], tenants: tuple[Tenant, Tenant], viewer: uuid.UUID
) -> None:
    """`can_write_project()` is otherwise entirely untested, and write isolation
    would be assumed rather than demonstrated."""
    a, _ = tenants
    as_user(conn, viewer)

    assert _count(conn, "issues", "project_id", a.project_id) >= 1, "viewer cannot read"

    with conn.cursor() as cur:
        cur.execute("select rt_auth.can_write_project(%s)", (a.project_id,))
        row = cur.fetchone()
        assert row is not None and row[0] is False

        cur.execute("update issues set status = 'resolved' where project_id = %s", (a.project_id,))
        assert cur.rowcount == 0, "viewer mutated a row they may only read"


def test_dropping_policies_is_caught_by_the_positive_control(
    conn: psycopg.Connection[Any], tenants: tuple[Tenant, Tenant]
) -> None:
    """The suite has teeth (SC12) — and which test bites is not the obvious one.

    RLS is default-deny, so removing policies does NOT open a leak: it closes
    the table to everyone. The negative test goes on passing, more emphatically
    than before. What notices is the POSITIVE control, which is a second reason
    it has to exist.

    Note also that dropping `tenant_read` alone changes nothing, because
    `tenant_write` is `for all` and its USING clause filters SELECT too. Both
    have to go before reads stop.
    """
    a, _ = tenants
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute("drop policy tenant_read on issues")

    as_user(conn, a.user_id)
    assert _count(conn, "issues", "project_id", a.project_id) >= 1, (
        "tenant_write's USING should still permit this read"
    )

    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute("drop policy tenant_write on issues")

    as_user(conn, a.user_id)
    assert _count(conn, "issues", "project_id", a.project_id) == 0, (
        "issues has no policies left and RLS is forced, so this must be "
        "default-deny. If rows are still visible, RLS is not being applied."
    )


def test_disabling_rls_produces_a_real_leak(
    conn: psycopg.Connection[Any], tenants: tuple[Tenant, Tenant]
) -> None:
    """The other half of the teeth check: prove the negative test CAN fail.

    A cross-tenant leak needs RLS switched off, not policies removed. If this
    does not leak, `test_rls_blocks_cross_tenant_read` is passing for some
    reason other than the policy — a missing grant, an empty table, a typo in
    the scope column — and is worthless.
    """
    a, b = tenants
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute("alter table issues disable row level security")

    as_user(conn, a.user_id)
    assert _count(conn, "issues", "project_id", b.project_id) >= 1, (
        "Disabling RLS on issues did NOT expose tenant B's rows. The negative "
        "isolation test cannot fail, so it proves nothing."
    )


def test_tenant_table_list_matches_database(conn: psycopg.Connection[Any]) -> None:
    """The 26 in `18` §6, the migration assertion, and this list must agree."""
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute(
            """select c.relname
                 from pg_class c join pg_namespace n on n.oid = c.relnamespace
                where n.nspname = 'public' and c.relkind in ('r','p') and c.relrowsecurity
                  and not exists (select 1 from pg_inherits i where i.inhrelid = c.oid)"""
        )
        in_db = {r[0] for r in cur.fetchall()}
    assert in_db == set(TENANT_TABLES), (
        f"only in database: {sorted(in_db - set(TENANT_TABLES))}; "
        f"only in TENANT_TABLES: {sorted(set(TENANT_TABLES) - in_db)}"
    )
    assert len(TENANT_TABLES) == 26
