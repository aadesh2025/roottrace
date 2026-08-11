"""Architecture regression guards for the authorization model.

`docs/14` §4.1a, `docs/11` §4 layer 1a. B2 and B4 both arose from
plausible-looking code that no functional test would have caught, so these
assert the *shape* of the model rather than its behaviour.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from tests.conftest import as_postgres, as_user

pytestmark = [pytest.mark.integration, pytest.mark.security]

RT_AUTH_HELPERS = (
    "uid",
    "org_ids",
    "project_ids",
    "can_write_project",
    "is_project_admin",
    "is_org_owner",
)


def test_no_role_holds_bypassrls(conn: psycopg.Connection[Any]) -> None:
    """ADR-009 Option B. The only BYPASSRLS roles are Supabase's own.

    `rt_rls_owner` must not reappear. If a future change needs it, that is a
    decision to record in ADR-009, not a migration to slip in.
    """
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute("select rolname from pg_roles where rolbypassrls and rolname like 'rt%'")
        ours = [r[0] for r in cur.fetchall()]
    assert ours == [], f"roles holding BYPASSRLS: {ours}"


def test_no_rt_auth_helper_is_security_definer(conn: psycopg.Connection[Any]) -> None:
    """No helper runs with authority the caller does not already hold."""
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute(
            """select proname from pg_proc
                where pronamespace = 'rt_auth'::regnamespace and prosecdef
                  and proname = any(%s)""",
            (list(RT_AUTH_HELPERS),),
        )
        definers = [r[0] for r in cur.fetchall()]
    assert definers == [], f"SECURITY DEFINER helpers: {definers}"


def test_all_definer_functions_pin_search_path(conn: psycopg.Connection[Any]) -> None:
    """An unpinned SECURITY DEFINER function can be fed a shadowed table."""
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute(
            """select n.nspname || '.' || p.proname
                 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                where p.prosecdef
                  and n.nspname in ('public','rt_auth','rt_admin')
                  and (p.proconfig is null
                       or not exists (select 1 from unnest(p.proconfig) c
                                       where c like 'search_path=%'))"""
        )
        unpinned = [r[0] for r in cur.fetchall()]
    assert unpinned == [], f"SECURITY DEFINER without a pinned search_path: {unpinned}"


def test_no_helper_takes_a_user_id(conn: psycopg.Connection[Any]) -> None:
    """ "What can someone else see" must not be expressible.

    Every helper derives identity from rt_auth.uid() internally. A helper taking
    a user id would let any caller ask about any user.
    """
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute(
            """select proname, pg_get_function_arguments(oid)
                 from pg_proc where pronamespace = 'rt_auth'::regnamespace"""
        )
        offenders = [
            f"{name}({args})"
            for name, args in cur.fetchall()
            if "user" in args.lower() or args.strip() in ("uid uuid", "user_id uuid")
        ]
    assert offenders == [], f"helpers accepting a user identifier: {offenders}"


def test_anon_cannot_execute_rt_auth(conn: psycopg.Connection[Any]) -> None:
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute(
            """select p.proname
                 from pg_proc p
                where p.pronamespace = 'rt_auth'::regnamespace
                  and has_function_privilege('anon', p.oid, 'EXECUTE')"""
        )
        callable_by_anon = [r[0] for r in cur.fetchall()]
    assert callable_by_anon == [], f"anon can execute: {callable_by_anon}"


def test_membership_policies_are_own_row_only(conn: psycopg.Connection[Any]) -> None:
    """The property that removes the recursion, and therefore the privileged role.

    A co-member clause here is what forced BYPASSRLS in the original design; it
    raises `infinite recursion detected in policy` when written inline. If one
    reappears — with team invites in V2 — that is a decision for ADR-009, not a
    policy edit.
    """
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute(
            """select polrelid::regclass::text, pg_get_expr(polqual, polrelid)
                 from pg_policy
                where polrelid in ('project_members'::regclass,
                                   'organization_members'::regclass)
                  and polcmd = 'r'"""
        )
        for table, expr in cur.fetchall():
            assert "uid()" in expr, f"{table}: read policy does not key on the caller: {expr}"
            assert table.split(".")[-1] not in expr, (
                f"{table}: read policy references its own table — this is the "
                f"recursion ADR-009 removed: {expr}"
            )


def test_projects_write_policies_are_per_command(conn: psycopg.Connection[Any]) -> None:
    """A `for all` policy's USING is evaluated on SELECT too, so `for all using
    can_write_project(id)` makes every read of projects call a function that
    reads projects: `stack depth limit exceeded`."""
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute(
            """select polname, polcmd from pg_policy
                where polrelid = 'projects'::regclass and polcmd = '*'"""
        )
        for_all = [r[0] for r in cur.fetchall()]
    assert for_all == [], f"projects has `for all` policies, which recurse on SELECT: {for_all}"


def test_matviews_are_unreadable_directly(conn: psycopg.Connection[Any]) -> None:
    """B6: a matview cannot carry RLS, so direct access is an aggregate leak."""
    as_postgres(conn)
    for view in ("issue_hourly_counts", "project_health_daily"):
        with conn.cursor() as cur:
            cur.execute("select has_table_privilege('authenticated', %s, 'SELECT')", (view,))
            row = cur.fetchone()
        assert row is not None and row[0] is False, f"{view} is readable by authenticated"


def test_audit_log_is_append_only(conn: psycopg.Connection[Any]) -> None:
    as_postgres(conn)
    with conn.cursor() as cur:
        for privilege in ("UPDATE", "DELETE"):
            cur.execute(
                "select has_table_privilege(%s, 'audit_log', %s)", ("service_role", privilege)
            )
            row = cur.fetchone()
            assert row is not None and row[0] is False, (
                f"service_role can {privilege} audit_log; the log is not immutable"
            )


def test_org_scoped_audit_rows_are_visible_to_the_org_owner(
    conn: psycopg.Connection[Any], tenants: Any
) -> None:
    """B5: project_id is nullable, and `NULL IN (...)` is NULL, never true — so
    these rows would otherwise be invisible to everyone forever."""
    a, b = tenants
    as_postgres(conn)
    with conn.cursor() as cur:
        cur.execute(
            """insert into audit_log (project_id, organization_id, action, actor_type)
               values (null, %s, 'installation.created', 'github_app')""",
            (a.org_id,),
        )

    as_user(conn, a.user_id)
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from audit_log where organization_id = %s and project_id is null",
            (a.org_id,),
        )
        mine = cur.fetchone()
    assert mine is not None and mine[0] == 1, "org owner cannot see their org-scoped audit rows"

    as_user(conn, b.user_id)
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from audit_log where organization_id = %s and project_id is null",
            (a.org_id,),
        )
        theirs = cur.fetchone()
    assert theirs is not None and theirs[0] == 0, "org-scoped audit rows leaked to another org"
