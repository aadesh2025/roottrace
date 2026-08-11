-- 001000 — grants, then row-level security on all 26 tenant tables.
--
-- docs/04-DATA-MODEL.md §12.4-12.8.
--
-- ═══════════════════════════════════════════════════════════════════════════
-- PART 1 — GRANTS. Read this before assuming it is boilerplate.
-- ═══════════════════════════════════════════════════════════════════════════
--
-- RLS filters rows WITHIN what a role is granted. It grants nothing itself. The
-- Supabase CLI no longer auto-exposes new tables to the Data API roles
-- (`auto_expose_new_tables` is unset in config.toml, matching the current cloud
-- default), so without the statements below `authenticated` would hold no
-- privilege on any table here.
--
-- The dashboard would fail with `permission denied`, which is merely annoying.
-- The dangerous consequence is the other one: every cross-tenant isolation test
-- would return zero rows and PASS — because of a missing grant, not because a
-- policy filtered anything. Those tests would stay green with every policy in
-- this file dropped. T1.3 therefore pairs each negative test with a positive
-- control proving the same role CAN read its own project's rows.
--
-- Grants are enumerated, never blanket. SELECT is broad because the dashboard
-- reads the whole pipeline; write is confined to the surfaces a human actually
-- writes from. Everything else is worker-written as service_role.

-- ── Read ────────────────────────────────────────────────────────────────────
grant select on
  organizations, organization_members, projects, project_members, api_keys,
  github_installations, repositories, code_nodes, code_edges,
  raw_events, issues, error_occurrences, investigations, pipeline_steps,
  llm_calls, context_bundles, root_cause_analyses, patches, validation_runs,
  critiques, confidence_scores, pull_request_records, feedback_events,
  investigation_messages, audit_log, usage_daily
to authenticated;

-- ── Write ───────────────────────────────────────────────────────────────────
-- Settings surfaces, issue triage, and V4 chat. Nothing else: patches,
-- critiques, scores and validation runs are produced by the pipeline and a human
-- must not be able to forge one.
grant insert, update, delete on
  projects, api_keys, issues, investigation_messages
to authenticated;

-- Membership tables. These are granted for the same reason the read grants
-- matter: docs/14 §4.1a asserts that a maintainer CANNOT escalate to owner and
-- that a non-member CANNOT add themselves. Without a grant those attempts fail
-- on privilege before any policy is consulted, and the B4 suite would pass
-- while proving nothing about the policies it exists to test.
--
-- (Team invites are out of V1 scope, so no UI writes these yet. The grant is
-- here so the policies are reachable and therefore testable.)
grant insert, update, delete on
  organizations, organization_members, project_members
to authenticated;

-- audit_log is append-only and only the worker appends (§12.7). No UPDATE or
-- DELETE grant exists for anyone — not even service_role.
revoke insert, update, delete on audit_log from authenticated;
revoke         update, delete on audit_log from service_role;

-- ═══════════════════════════════════════════════════════════════════════════
-- PART 2 — Bespoke policies for the 6 tables not keyed on a plain project_id.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── organizations: organization-scoped, keyed on id ─────────────────────────
alter table organizations enable row level security;
alter table organizations force  row level security;

drop policy if exists organizations_read on organizations;
create policy organizations_read on organizations for select
  using (id in (select rt_auth.org_ids()));

drop policy if exists organizations_write on organizations;
create policy organizations_write on organizations for all
  using      (rt_auth.is_org_owner(id))
  with check (rt_auth.is_org_owner(id));

-- ── organization_members: own row, or co-members of your orgs ───────────────
alter table organization_members enable row level security;
alter table organization_members force  row level security;

drop policy if exists org_members_read on organization_members;
create policy org_members_read on organization_members for select
  using (user_id = rt_auth.uid() or organization_id in (select rt_auth.org_ids()));

-- Writes are gated on is_org_owner — deliberately narrower than write authority.
drop policy if exists org_members_insert on organization_members;
create policy org_members_insert on organization_members for insert
  with check (rt_auth.is_org_owner(organization_id));

-- WITH CHECK on UPDATE as well as USING, so an owner cannot move a row INTO an
-- organization they do not own.
drop policy if exists org_members_update on organization_members;
create policy org_members_update on organization_members for update
  using      (rt_auth.is_org_owner(organization_id))
  with check (rt_auth.is_org_owner(organization_id));

drop policy if exists org_members_delete on organization_members;
create policy org_members_delete on organization_members for delete
  using (rt_auth.is_org_owner(organization_id));

-- ── github_installations: organization-scoped, no project_id column ─────────
-- Worker-only in V1; the policy exists now so the V2 dashboard read is safe on
-- the day it is written rather than on the day someone remembers.
alter table github_installations enable row level security;
alter table github_installations force  row level security;

drop policy if exists gh_installations_read on github_installations;
create policy gh_installations_read on github_installations for select
  using (organization_id in (select rt_auth.org_ids()));

drop policy if exists gh_installations_write on github_installations;
create policy gh_installations_write on github_installations for all
  using      (rt_auth.is_org_owner(organization_id))
  with check (rt_auth.is_org_owner(organization_id));

-- ── project_members: the escalation surface ─────────────────────────────────
-- Whoever can write this table can grant themselves anything, so writes are
-- gated on is_project_admin (owner only), not can_write_project. A maintainer
-- has no write path here at all — "maintainer promotes self to owner" is an
-- absent capability, not a policy nuance (B4).
alter table project_members enable row level security;
alter table project_members force  row level security;

drop policy if exists project_members_read on project_members;
create policy project_members_read on project_members for select
  using (user_id = rt_auth.uid() or project_id in (select rt_auth.project_ids()));

drop policy if exists project_members_insert on project_members;
create policy project_members_insert on project_members for insert
  with check (rt_auth.is_project_admin(project_id));

drop policy if exists project_members_update on project_members;
create policy project_members_update on project_members for update
  using      (rt_auth.is_project_admin(project_id))
  with check (rt_auth.is_project_admin(project_id));

drop policy if exists project_members_delete on project_members;
create policy project_members_delete on project_members for delete
  using (rt_auth.is_project_admin(project_id));

-- ── projects: keyed on id, never in the generic loop (B1) ───────────────────
-- `projects` has no project_id column; its identity IS id. Running it through
-- the loop is what the original design got wrong.
alter table projects enable row level security;
alter table projects force  row level security;

drop policy if exists projects_read on projects;
create policy projects_read on projects for select
  using (id in (select rt_auth.project_ids()));

drop policy if exists projects_write on projects;
create policy projects_write on projects for all
  using      (rt_auth.can_write_project(id))
  with check (rt_auth.can_write_project(id));

-- ── audit_log: dual-scope (B5) ──────────────────────────────────────────────
-- No row may be unattributable to a tenant.
alter table audit_log drop constraint if exists audit_log_scope_ck;
alter table audit_log add constraint audit_log_scope_ck
  check (project_id is not null or organization_id is not null);

alter table audit_log enable row level security;
alter table audit_log force  row level security;

drop policy if exists audit_read on audit_log;
create policy audit_read on audit_log for select
  using (
       (project_id is not null and project_id in (select rt_auth.project_ids()))
    or (project_id is null and organization_id is not null
        and rt_auth.is_org_owner(organization_id))
  );
-- Organization-level events are visible to org OWNERS only — deliberately
-- narrower than project events, because they concern the installation and
-- billing surface rather than one project's activity.
--
-- No UPDATE or DELETE policy exists at all, which together with the revokes
-- above is what makes the log append-only.

-- ═══════════════════════════════════════════════════════════════════════════
-- PART 3 — The generic loop: the 20 tables with a real project_id column.
-- ═══════════════════════════════════════════════════════════════════════════

do $$
declare t text;
begin
  foreach t in array array[
    'api_keys','repositories','code_nodes','code_edges',
    'raw_events','issues','error_occurrences','investigations','pipeline_steps',
    'llm_calls','context_bundles','root_cause_analyses','patches','validation_runs',
    'critiques','confidence_scores','pull_request_records','feedback_events',
    'investigation_messages','usage_daily'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('alter table public.%I force  row level security', t);

    execute format('drop policy if exists tenant_read on public.%I', t);
    execute format($f$
      create policy tenant_read on public.%I for select
        using (project_id in (select rt_auth.project_ids()))
    $f$, t);

    execute format('drop policy if exists tenant_write on public.%I', t);
    execute format($f$
      create policy tenant_write on public.%I for all
        using      (project_id in (select rt_auth.project_ids())
                    and rt_auth.can_write_project(project_id))
        with check (project_id in (select rt_auth.project_ids())
                    and rt_auth.can_write_project(project_id))
    $f$, t);
  end loop;
end $$;

-- The 6 bespoke tables are excluded from this loop by design: none of them is
-- keyed on a plain project_id. 20 + 6 = 26, and T1.3 asserts that arithmetic so
-- a newly added table cannot be silently forgotten.
--
-- NOTE ON PARTITIONS: the loop applies RLS to the partitioned PARENTS
-- (raw_events, error_occurrences). Partitions are separate relations and
-- inherit none of this — see 001200 and 001400.
