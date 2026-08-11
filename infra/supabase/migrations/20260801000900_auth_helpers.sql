-- 000900 — the rt_auth schema and the six authorization helpers.
--
-- docs/04-DATA-MODEL.md §12.2, ADR-009. Must precede 001000: every policy there
-- references rt_auth.*, and PostgreSQL resolves those references at CREATE
-- POLICY time.
--
-- ═══════════════════════════════════════════════════════════════════════════
-- NO BYPASSRLS. NO PRIVILEGED ROLE. (ADR-009, Option B — taken)
-- ═══════════════════════════════════════════════════════════════════════════
-- The original design gave these helpers SECURITY DEFINER and owned them with
-- `rt_rls_owner`, a role created solely to hold BYPASSRLS, because B2's cycle
-- was real: project_ids() read `projects`, whose policy called project_ids().
--
-- The cycle only exists if a membership policy has to read OTHER people's
-- membership rows. It does not:
--
--   * Every helper below asks a question about the CALLER — which projects am I
--     in, may I write here, am I an owner. All of them are answerable from the
--     caller's own rows.
--   * The membership policies are therefore `user_id = rt_auth.uid()`: own row
--     only, no subquery, nothing to recurse into.
--   * `projects`, `organizations` and the 20 generic tables read the membership
--     tables under that policy, which terminates in one hop.
--
-- Measured before adopting (ADR-009): an inline co-member clause raises
-- `infinite recursion detected in policy`, directly and mutually; own-row-only
-- passes clean across all 21 inline tables.
--
-- What we give up is co-member visibility — seeing who ELSE is on your project.
-- V1 has no way to add a second member (team invites are V2), so a project has
-- exactly one member and own-row-only shows the complete roster. The capability
-- is not reduced; the privileged role is simply not needed to deliver it.
--
-- Consequently these are PLAIN functions: stable, not SECURITY DEFINER, owned by
-- nobody special, executing as the caller under the caller's own policies.

create schema if not exists rt_auth;
revoke all on schema rt_auth from public;
grant usage on schema rt_auth to authenticated;

-- The caller's identity, read straight from the request JWT.
--
-- Not auth.uid(): that lives in the `auth` schema, and a function that needed
-- USAGE there could not be granted it — `grant usage on schema auth` run as
-- `postgres` emits `WARNING: no privileges were granted`, because the schema
-- belongs to supabase_admin. A warning does not fail a migration, so the fix
-- appears to apply and does nothing. auth.uid() is itself a one-line read of
-- the same claim, so we read it directly and depend on no other schema.
create or replace function rt_auth.uid() returns uuid
language sql stable
set search_path = pg_catalog
as $$
  select nullif(current_setting('request.jwt.claims', true)::jsonb ->> 'sub', '')::uuid
$$;

-- Organizations the caller belongs to. Reads only the caller's own rows.
create or replace function rt_auth.org_ids() returns setof uuid
language sql stable
set search_path = pg_catalog, public          -- pinned: defeats search_path injection
as $$
  select om.organization_id
    from public.organization_members om
   where om.user_id = rt_auth.uid()
$$;

-- Projects the caller can see: direct membership, or membership of the owning
-- org. The read of `projects` is itself filtered by projects' own policy, which
-- reads only membership rows — one hop, no cycle.
create or replace function rt_auth.project_ids() returns setof uuid
language sql stable
set search_path = pg_catalog, public
as $$
  select pm.project_id
    from public.project_members pm
   where pm.user_id = rt_auth.uid()
  union
  select p.id
    from public.projects p
   where p.deleted_at is null
     and p.organization_id in (select om.organization_id
                                 from public.organization_members om
                                where om.user_id = rt_auth.uid())
$$;

-- Write authority on a project: project owner/maintainer, or org owner/maintainer.
create or replace function rt_auth.can_write_project(pid uuid) returns boolean
language sql stable
set search_path = pg_catalog, public
as $$
  select exists (select 1 from public.project_members pm
                  where pm.project_id = pid and pm.user_id = rt_auth.uid()
                    and pm.role in ('owner','maintainer'))
      or exists (select 1 from public.projects p
                   join public.organization_members om
                     on om.organization_id = p.organization_id
                  where p.id = pid and om.user_id = rt_auth.uid()
                    and om.role in ('owner','maintainer'))
$$;

-- Administrative authority: may alter MEMBERSHIP. Strictly narrower than write,
-- and that gap is what makes "maintainer promotes self to owner" an absent
-- capability rather than a policy nuance (B4).
create or replace function rt_auth.is_project_admin(pid uuid) returns boolean
language sql stable
set search_path = pg_catalog, public
as $$
  select exists (select 1 from public.project_members pm
                  where pm.project_id = pid and pm.user_id = rt_auth.uid()
                    and pm.role = 'owner')
      or exists (select 1 from public.projects p
                   join public.organization_members om
                     on om.organization_id = p.organization_id
                  where p.id = pid and om.user_id = rt_auth.uid()
                    and om.role = 'owner')
$$;

create or replace function rt_auth.is_org_owner(oid uuid) returns boolean
language sql stable
set search_path = pg_catalog, public
as $$
  select exists (select 1 from public.organization_members om
                  where om.organization_id = oid and om.user_id = rt_auth.uid()
                    and om.role = 'owner')
$$;

-- anon cannot call these at all.
revoke execute on all functions in schema rt_auth from public;
grant  execute on all functions in schema rt_auth to authenticated;

-- Properties that keep this from being an escalation path, each checked in T1.3:
--   1. No helper accepts a user identifier. Every one derives identity from
--      rt_auth.uid(), so "what can someone else see" is not expressible.
--   2. search_path is pinned on every function, so a caller cannot shadow
--      public.project_members with a temp table.
--   3. EXECUTE is revoked from PUBLIC and granted only to authenticated.
--   4. None of them is SECURITY DEFINER, so none runs with privileges the
--      caller does not already hold. This is the property the previous design
--      could not offer, and it is why the four regression guards in `14` §4.1a
--      matter less than they used to.
