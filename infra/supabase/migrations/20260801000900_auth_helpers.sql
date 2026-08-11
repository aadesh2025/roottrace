-- 000900 — rt_rls_owner, the rt_auth schema, and the five authorization helpers.
--
-- docs/04-DATA-MODEL.md §12.2, ADR-009. Must precede 001000: every policy there
-- references rt_auth.*, and PostgreSQL resolves those references at CREATE
-- POLICY time.
--
-- ── Why a dedicated role exists (B2) ────────────────────────────────────────
-- SECURITY DEFINER does NOT bypass RLS. Only a role holding BYPASSRLS does
-- (C12 corrected the opposite claim). The original design deadlocked on exactly
-- this: auth_project_ids() read `projects`, whose policy called
-- auth_project_ids(), and SECURITY DEFINER did not break the cycle.
--
-- rt_rls_owner exists solely to hold BYPASSRLS. It cannot log in and owns
-- nothing except these functions.

do $$ begin
  create role rt_rls_owner nologin bypassrls;
exception when duplicate_object then null; end $$;

-- Migrations run as `postgres`, which on Supabase is NOT a superuser. Creating a
-- schema owned by another role, and reassigning function ownership below, both
-- require membership in that role:
--   ERROR: must be able to SET ROLE "rt_rls_owner" (SQLSTATE 42501)
-- PostgreSQL 16+ grants the creating role ADMIN OPTION automatically, so this
-- succeeds for whoever ran the CREATE ROLE above.
do $$ begin
  execute format('grant rt_rls_owner to %I', current_user);
end $$;

create schema if not exists rt_auth authorization rt_rls_owner;
revoke all on schema rt_auth from public;
grant usage on schema rt_auth to authenticated;

-- ── Why rt_auth.uid() exists instead of auth.uid() (deviation from `04` §12.2)
--
-- The helpers below are SECURITY DEFINER and therefore execute as rt_rls_owner.
-- `04` §12.2 has them call auth.uid(), but rt_rls_owner has no USAGE on the
-- `auth` schema, so every policy in the system fails at QUERY time with
--   ERROR: permission denied for schema auth
--
-- The obvious repair does not work: `grant usage on schema auth to rt_rls_owner`
-- run as `postgres` emits `WARNING: no privileges were granted for "auth"` and
-- changes nothing, because the schema is owned by supabase_admin and postgres
-- holds no grant option on it. A WARNING does not fail a migration — so the fix
-- appears to apply, and does nothing. Hosted Supabase is more restrictive, not
-- less, so this would not have behaved differently there.
--
-- auth.uid() is itself a one-line read of the request JWT, so we read it
-- directly and depend on no other schema. Everything that needs the caller's
-- identity now uses this single definition, including the two membership
-- policies in 001000 that `04` §12.4 writes as auth.uid().
--
-- Worth recording how this was caught: both coverage assertions passed, because
-- they check STRUCTURE — RLS enabled, forced, policy present — not that a policy
-- can be evaluated. Only the positive-control read found it. A purely negative
-- isolation suite cannot: "user A sees zero of B's rows" is equally true when
-- nothing works at all.
create or replace function rt_auth.uid() returns uuid
language sql stable
set search_path = pg_catalog
as $$
  select nullif(current_setting('request.jwt.claims', true)::jsonb ->> 'sub', '')::uuid
$$;

-- Organizations the caller belongs to.
create or replace function rt_auth.org_ids() returns setof uuid
language sql stable security definer
set search_path = pg_catalog, public          -- pinned: defeats search_path injection
as $$
  select om.organization_id
    from public.organization_members om
   where om.user_id = rt_auth.uid()
$$;

-- Projects the caller can see: direct membership, or membership of the owning org.
create or replace function rt_auth.project_ids() returns setof uuid
language sql stable security definer
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
language sql stable security definer
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
language sql stable security definer
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
language sql stable security definer
set search_path = pg_catalog, public
as $$
  select exists (select 1 from public.organization_members om
                  where om.organization_id = oid and om.user_id = rt_auth.uid()
                    and om.role = 'owner')
$$;

-- Ownership is what actually confers the bypass — the functions must be owned by
-- the BYPASSRLS role, not merely declared SECURITY DEFINER.
-- rt_auth.uid() included: the five helpers call it while running AS
-- rt_rls_owner, and the blanket `revoke execute ... from public` below would
-- otherwise leave that role unable to execute it —
--   ERROR: permission denied for function uid
-- A function's owner always retains EXECUTE, so ownership is the fix rather
-- than another grant to maintain.
alter function rt_auth.uid()                   owner to rt_rls_owner;
alter function rt_auth.org_ids()               owner to rt_rls_owner;
alter function rt_auth.project_ids()           owner to rt_rls_owner;
alter function rt_auth.can_write_project(uuid) owner to rt_rls_owner;
alter function rt_auth.is_project_admin(uuid)  owner to rt_rls_owner;
alter function rt_auth.is_org_owner(uuid)      owner to rt_rls_owner;

-- BYPASSRLS exempts a role from POLICIES. It confers no table privileges
-- whatsoever, and `04` §12.2 does not mention the difference:
--   ERROR: permission denied for table project_members
-- These three tables are everything the helpers read, and read is all they get.
grant select on public.organization_members, public.project_members, public.projects
  to rt_rls_owner;

-- anon cannot call these at all.
revoke execute on all functions in schema rt_auth from public;
grant  execute on all functions in schema rt_auth to authenticated;

-- Four properties make this not an escalation path, each checked by a test in
-- T1.3:
--   1. No helper accepts a user identifier. Every one derives identity from
--      rt_auth.uid() internally, so "what can someone else see" is not expressible.
--   2. search_path is pinned on every function, so a caller cannot shadow
--      public.project_members with a temp table and feed attacker-controlled
--      rows to a BYPASSRLS function.
--   3. EXECUTE is revoked from PUBLIC and granted only to authenticated.
--   4. The owner role has no login and owns nothing else; compromising it
--      requires already being superuser.
