"""Integration-test fixtures: a real database, two real tenants.

Runs against the Supabase-managed Postgres rather than a bare `testcontainers`
Postgres, which is a deliberate deviation from `docs/14` §4. The schema depends
on Supabase-provided roles (`authenticated`, `anon`, `service_role`) and the
`auth` schema; a stock Postgres image has none of them, so the migrations cannot
apply and the tests would be exercising a different database from the one we
ship. `make db-reset` gives every run the same known state.
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

# psycopg's async driver cannot run on Windows' default ProactorEventLoop:
# "Psycopg cannot use the 'ProactorEventLoop' to run in async mode." CI is
# Linux and unaffected, and production runs Linux too — but a developer on
# Windows would otherwise see every async database test fail for a reason that
# has nothing to do with the code under test.
#
# Set here rather than in application code: the constraint belongs to the
# process that hosts the loop, and `serve.py` on Linux must not carry a
# Windows workaround.
if sys.platform == "win32":  # pragma: no cover — platform-specific
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

DEFAULT_DSN = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"

# The 26 logical tenant tables, in dependency order. `18` §6 fixes the count;
# the migration's third assertion fails if the database disagrees, and this list
# failing to match is caught by test_tenant_table_list_matches_database.
TENANT_TABLES: tuple[str, ...] = (
    "organizations",
    "organization_members",
    "projects",
    "project_members",
    "api_keys",
    "github_installations",
    "repositories",
    "code_nodes",
    "code_edges",
    "raw_events",
    "issues",
    "error_occurrences",
    "investigations",
    "pipeline_steps",
    "llm_calls",
    "context_bundles",
    "root_cause_analyses",
    "patches",
    "validation_runs",
    "critiques",
    "confidence_scores",
    "pull_request_records",
    "feedback_events",
    "investigation_messages",
    "audit_log",
    "usage_daily",
)

# How to identify one tenant's rows in each table. Most are keyed on project_id;
# the identity tables are not, which is exactly why they need bespoke policies.
SCOPE_COLUMN: dict[str, str] = {
    "organizations": "id",
    "organization_members": "organization_id",
    "github_installations": "organization_id",
    "projects": "id",
}


def _dsn() -> str:
    return os.environ.get("RT_DATABASE_URL", DEFAULT_DSN)


@pytest.fixture(scope="session")
def dsn() -> str:
    return _dsn()


@pytest.fixture
def conn(dsn: str) -> Iterator[psycopg.Connection[Any]]:
    """A connection whose work is always rolled back.

    Every test runs inside one transaction that is never committed, so tests are
    order-independent and parallel-safe without truncating between them.
    """
    with psycopg.connect(dsn, autocommit=False) as connection:
        yield connection
        connection.rollback()


class Tenant:
    """One fully populated tenant: a row in all 26 tables."""

    def __init__(self, label: str) -> None:
        self.label = label
        self.user_id = uuid.uuid4()
        self.org_id = uuid.uuid4()
        self.project_id = uuid.uuid4()
        self.issue_id = uuid.uuid4()
        self.investigation_id = uuid.uuid4()
        self.repository_id = uuid.uuid4()
        self.installation_id = uuid.uuid4()
        self.node_id = uuid.uuid4()
        self.patch_id = uuid.uuid4()
        self.step_id = uuid.uuid4()

    def scope_value(self, table: str) -> uuid.UUID:
        column = SCOPE_COLUMN.get(table, "project_id")
        if column in ("id", "project_id") and table != "organizations":
            return self.org_id if column == "organization_id" else self.project_id
        if table == "organizations":
            return self.org_id
        if column == "organization_id":
            return self.org_id
        return self.project_id


def _seed(cur: psycopg.Cursor[Any], t: Tenant, *, gh_installation_seq: int) -> None:
    """Insert exactly one row into each of the 26 tables for this tenant.

    Runs as the migration role, which is not subject to RLS — seeding must not
    depend on the thing under test.
    """
    now = datetime.now(UTC)

    cur.execute(
        """insert into auth.users (id, instance_id, aud, role, email,
                                   encrypted_password, email_confirmed_at,
                                   created_at, updated_at)
           values (%s, '00000000-0000-0000-0000-000000000000', 'authenticated',
                   'authenticated', %s, 'x', now(), now(), now())""",
        (t.user_id, f"{t.label}-{t.user_id}@example.test"),
    )
    cur.execute(
        "insert into organizations (id, name, slug) values (%s, %s, %s)",
        (t.org_id, f"Org {t.label}", f"org-{t.label}-{t.org_id.hex[:8]}"),
    )
    cur.execute(
        "insert into organization_members (organization_id, user_id, role) values (%s,%s,'owner')",
        (t.org_id, t.user_id),
    )
    cur.execute(
        "insert into projects (id, organization_id, name, slug) values (%s,%s,%s,%s)",
        (t.project_id, t.org_id, f"Project {t.label}", f"p-{t.label}"),
    )
    cur.execute(
        "insert into project_members (project_id, user_id, role) values (%s,%s,'owner')",
        (t.project_id, t.user_id),
    )
    cur.execute(
        """insert into api_keys (project_id, name, key_prefix, key_hash)
           values (%s, 'k', 'rt_live_REPLACE_ME', %s)""",
        (t.project_id, f"hash-{t.project_id}"),
    )
    cur.execute(
        """insert into github_installations
             (id, organization_id, installation_id, account_login, account_type,
              repository_selection, permissions, events)
           values (%s,%s,%s,'acme','Organization','selected','{}','{}')""",
        (t.installation_id, t.org_id, gh_installation_seq),
    )
    cur.execute(
        """insert into repositories (id, project_id, installation_id, github_repo_id, full_name)
           values (%s,%s,%s,%s,%s)""",
        (t.repository_id, t.project_id, t.installation_id, gh_installation_seq, f"acme/{t.label}"),
    )
    cur.execute(
        """insert into code_nodes (id, project_id, repository_id, repo_path, symbol_name, kind,
                                   language, start_line, end_line, source, source_hash, commit_sha)
           values (%s,%s,%s,'a.py','f','function','python',1,2,'src','h','sha')""",
        (t.node_id, t.project_id, t.repository_id),
    )
    cur.execute(
        """insert into code_edges (project_id, repository_id, from_node_id, kind)
           values (%s,%s,%s,'calls')""",
        (t.project_id, t.repository_id, t.node_id),
    )
    cur.execute(
        """insert into raw_events (project_id, batch_id, event_ts, environment, payload,
                                   payload_bytes)
           values (%s, gen_random_uuid(), %s, 'production', '{}', 2)""",
        (t.project_id, now),
    )
    cur.execute(
        """insert into issues (id, project_id, fingerprint, error_type, normalized_message,
                               sample_message, first_seen, last_seen)
           values (%s,%s,%s,'TypeError','m','m',%s,%s)""",
        (t.issue_id, t.project_id, f"fp-{t.label}", now, now),
    )
    cur.execute(
        """insert into error_occurrences (project_id, issue_id, raw_event_id, occurred_at,
                                          environment)
           values (%s,%s,gen_random_uuid(),%s,'production')""",
        (t.project_id, t.issue_id, now),
    )
    cur.execute(
        "insert into investigations (id, project_id, issue_id) values (%s,%s,%s)",
        (t.investigation_id, t.project_id, t.issue_id),
    )
    cur.execute(
        """insert into pipeline_steps (id, investigation_id, project_id, stage, sequence)
           values (%s,%s,%s,'receive',1)""",
        (t.step_id, t.investigation_id, t.project_id),
    )
    cur.execute(
        """insert into llm_calls (project_id, investigation_id, stage, tier, provider, model,
                                  prompt_version, prompt_url, response_url, prompt_hash,
                                  tokens_in, tokens_out, cost_micro_usd, latency_ms)
           values (%s,%s,'reason','reasoning_a','anthropic','claude-sonnet-5','v1',
                   's3://p','s3://r','ph',1,1,1,1)""",
        (t.project_id, t.investigation_id),
    )
    cur.execute(
        """insert into context_bundles (investigation_id, project_id, token_count, token_budget,
                                        file_count, files, content_url, graph, history, tests,
                                        strategy_stats, quality_score, quality_signals)
           values (%s,%s,10,24000,1,'[]','s3://c','{}','{}','{}','{}',0.5,'{}')""",
        (t.investigation_id, t.project_id),
    )
    cur.execute(
        """insert into root_cause_analyses (investigation_id, project_id, summary, mechanism,
                                            category, blast_radius, reasoning_chain, fix_strategy,
                                            evidence_validation, model, prompt_version)
           values (%s,%s,'s','m','c','{}','[]','{}','{}','claude-sonnet-5','v1')""",
        (t.investigation_id, t.project_id),
    )
    cur.execute(
        """insert into patches (id, investigation_id, project_id, base_commit, diff_url, diff_hash,
                                files_changed, total_additions, total_deletions, explanation,
                                risk_assessment, model, prompt_version)
           values (%s,%s,%s,'sha','s3://d','dh','[]',1,0,'e','{}','claude-sonnet-5','v1')""",
        (t.patch_id, t.investigation_id, t.project_id),
    )
    cur.execute(
        """insert into validation_runs (investigation_id, project_id, patch_id, passed, gates,
                                        signals_for_scoring, wall_ms, container_image)
           values (%s,%s,%s,true,'{}','{}',100,'roottrace/sandbox-python:3.12')""",
        (t.investigation_id, t.project_id, t.patch_id),
    )
    cur.execute(
        """insert into critiques (investigation_id, project_id, patch_id, verdict,
                                  agreement_with_diagnosis, addresses_reported_error,
                                  security_review, regression_risk, test_quality, model,
                                  prompt_version)
           values (%s,%s,%s,'approve',0.9,true,'{}','low','{}','gpt-5','v1')""",
        (t.investigation_id, t.project_id, t.patch_id),
    )
    cur.execute(
        """insert into confidence_scores (investigation_id, project_id, confidence, band,
                                          breakdown, explanation, should_publish, publish_mode)
           values (%s,%s,0.836,'high','{}','e',true,'draft')""",
        (t.investigation_id, t.project_id),
    )
    cur.execute(
        """insert into pull_request_records (investigation_id, project_id, branch_name, base_sha,
                                             title, body, is_simulated)
           values (%s,%s,'roottrace/fix','sha','t','b',true)""",
        (t.investigation_id, t.project_id),
    )
    cur.execute(
        """insert into feedback_events (investigation_id, project_id, outcome, decided_at)
           values (%s,%s,'merged_unchanged',%s)""",
        (t.investigation_id, t.project_id, now),
    )
    cur.execute(
        """insert into investigation_messages (investigation_id, project_id, role, content)
           values (%s,%s,'user','hello')""",
        (t.investigation_id, t.project_id),
    )
    cur.execute(
        "insert into audit_log (project_id, organization_id, action) values (%s,%s,'api_key.created')",
        (t.project_id, t.org_id),
    )
    cur.execute(
        "insert into usage_daily (project_id, day) values (%s, current_date)",
        (t.project_id,),
    )


@pytest.fixture
def tenants(conn: psycopg.Connection[Any]) -> tuple[Tenant, Tenant]:
    """Two complete, non-overlapping tenants: A and B."""
    a, b = Tenant("a"), Tenant("b")
    with conn.cursor() as cur:
        _seed(cur, a, gh_installation_seq=910_001)
        _seed(cur, b, gh_installation_seq=910_002)
    return a, b


@pytest.fixture
def viewer(conn: psycopg.Connection[Any], tenants: tuple[Tenant, Tenant]) -> uuid.UUID:
    """A `viewer` on project A — read authority, no write authority."""
    a, _ = tenants
    viewer_id = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            """insert into auth.users (id, instance_id, aud, role, email, encrypted_password,
                                       email_confirmed_at, created_at, updated_at)
               values (%s,'00000000-0000-0000-0000-000000000000','authenticated','authenticated',
                       %s,'x',now(),now(),now())""",
            (viewer_id, f"viewer-{viewer_id}@example.test"),
        )
        # Viewer on the project, and NOT a member of the organization — org
        # membership would confer write authority through can_write_project().
        cur.execute(
            "insert into project_members (project_id, user_id, role) values (%s,%s,'viewer')",
            (a.project_id, viewer_id),
        )
    return viewer_id


def as_user(conn: psycopg.Connection[Any], user_id: uuid.UUID) -> None:
    """Switch the session to `authenticated` carrying this user's JWT claims.

    This is the same path a dashboard request takes: PostgREST sets the role and
    the claims, and every policy resolves from `rt_auth.uid()`.
    """
    with conn.cursor() as cur:
        cur.execute("set local role authenticated")
        cur.execute(
            "select set_config('request.jwt.claims', %s, true)",
            (f'{{"sub":"{user_id}","role":"authenticated"}}',),
        )


def as_postgres(conn: psycopg.Connection[Any]) -> None:
    """Return to the migration role (not subject to RLS)."""
    with conn.cursor() as cur:
        cur.execute("reset role")


def month_start(offset_months: int = 0) -> datetime:
    """First instant of a month, relative to now."""
    now = datetime.now(UTC)
    year, month = now.year, now.month + offset_months
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return datetime(year, month, 1, tzinfo=UTC)


def days_before(reference: datetime, days: int) -> datetime:
    return reference - timedelta(days=days)
