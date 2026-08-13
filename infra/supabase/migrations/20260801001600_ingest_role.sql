-- ─────────────────────────────────────────────────────────────────────────────
-- The ingest write path.
--
-- Three binding rules collided, and this migration is how they are reconciled:
--
--   `03` §S1   S1 `receive` runs in the `api` service and INSERTs raw_events.
--   `A3` §6    the `api` must NOT hold the service-role key (boot invariant).
--   `04` §12   raw_events has FORCED RLS; `authenticated` holds SELECT only,
--              and every other write is "worker-written as service_role".
--
-- So the api cannot write as service_role, `authenticated` has no INSERT grant,
-- and there is no user JWT on the ingest path at all — it authenticates with an
-- API key (`05` §2.1), which belongs to a project rather than to a person.
--
-- `rt_ingest` is a role for exactly that path. It holds **no BYPASSRLS**, so
-- ADR-009's "no privileged role in the system" still holds, and it carries no
-- password: the api reaches it with `SET LOCAL ROLE`, so there is no second
-- credential to store, rotate or leak.
--
-- **The tenant scope is enforced by the database, not by the handler.** The api
-- sets `rt.project_id` from the API key it just verified, and the insert policy
-- pins every row to it. An application-layer bug — the wrong project on the
-- request object, a mixed-up loop variable — cannot write across tenants,
-- because the row would fail the WITH CHECK.
-- ─────────────────────────────────────────────────────────────────────────────

do $$ begin
  create role rt_ingest nologin;
exception when duplicate_object then null; end $$;

comment on role rt_ingest is
  'Ingest write path (docs/03 S1). No BYPASSRLS, no login: reached via SET LOCAL '
  'ROLE. Scoped to the project in rt.project_id by policy.';

-- `SET ROLE` requires membership, and Supabase's `postgres` is NOT a superuser
-- — which is the good news here. A superuser connection would bypass RLS
-- entirely and every policy below would be decorative, so the fact that this
-- grant is necessary is also what makes the scoping real.
--
-- In production the api connects as its own login role, which is granted
-- membership the same way. Membership is not the privilege: rt_ingest's grants
-- are, and they are three tables wide.
grant rt_ingest to postgres;

-- ── The current ingest project ──────────────────────────────────────────────
--
-- A GUC rather than a JWT claim because ingest has no user. `true` on
-- current_setting means "return null if unset" — without it an unset GUC raises
-- and every policy using it would fail closed with an unreadable error rather
-- than simply matching no rows.
create or replace function rt_auth.current_project() returns uuid
language sql stable
set search_path = pg_catalog
as $$
  select nullif(current_setting('rt.project_id', true), '')::uuid
$$;

comment on function rt_auth.current_project() is
  'The project an ingest request authenticated as, set by the api from a '
  'verified API key. NULL outside the ingest path.';

revoke execute on function rt_auth.current_project() from public;
grant  execute on function rt_auth.current_project() to rt_ingest, authenticated;

-- ── The dashboard's policies belong to the dashboard's role ─────────────────
--
-- `tenant_read` and `tenant_write` were created without a `TO` clause, so they
-- apply to every role. Permissive policies are OR'd but *all* of them are
-- evaluated, so an ingest INSERT also evaluated `tenant_write`'s WITH CHECK —
-- which calls `rt_auth.project_ids()`, which reads `project_members`.
--
-- Two ways out, and the choice matters. Granting rt_ingest execute on those
-- helpers and select on the membership tables would work, and would give a
-- write-only role read access to every project's membership to satisfy a
-- policy that was never meant for it.
--
-- Scoping the policies to `authenticated` is the honest fix: they encode what a
-- signed-in human may do, and rt_ingest is not one. It also removes the
-- cross-role evaluation entirely, so ingest is admitted or refused by exactly
-- one policy.
--
-- `authenticated` is unaffected — the same policies, the same expressions, now
-- naming the role they were always written for.
drop policy if exists tenant_read  on raw_events;
drop policy if exists tenant_write on raw_events;

create policy tenant_read on raw_events for select to authenticated
  using (project_id in (select rt_auth.project_ids()));

create policy tenant_write on raw_events for all to authenticated
  using      (project_id in (select rt_auth.project_ids())
              and rt_auth.can_write_project(project_id))
  with check (project_id in (select rt_auth.project_ids())
              and rt_auth.can_write_project(project_id));

-- The same scoping for the two tables ingest reads. `projects_read` is the
-- inline variant from ADR-009 and `api_keys` carries the standard pair; both
-- reach `project_members` through `rt_auth.uid()`, so an ingest SELECT would
-- otherwise need membership access to evaluate a policy that can only ever
-- return false for it.
--
-- Deliberately confined to the three tables rt_ingest touches rather than
-- applied across all 26. The wholesale change is probably right eventually, but
-- it would rewrite the policy set the entire T1.3 suite is built on, in a
-- migration whose subject is the ingest path.
drop policy if exists projects_read on projects;
create policy projects_read on projects for select to authenticated
  using (
       id in (select pm.project_id from project_members pm
               where pm.user_id = rt_auth.uid())
    or organization_id in (select om.organization_id from organization_members om
                            where om.user_id = rt_auth.uid())
  );

drop policy if exists tenant_read  on api_keys;
drop policy if exists tenant_write on api_keys;

create policy tenant_read on api_keys for select to authenticated
  using (project_id in (select rt_auth.project_ids()));

create policy tenant_write on api_keys for all to authenticated
  using      (project_id in (select rt_auth.project_ids())
              and rt_auth.can_write_project(project_id))
  with check (project_id in (select rt_auth.project_ids())
              and rt_auth.can_write_project(project_id));

-- ── Grants ──────────────────────────────────────────────────────────────────

grant usage on schema public, rt_auth to rt_ingest;

-- INSERT only, on one table. rt_ingest cannot read an investigation, a patch or
-- a setting, which is the same property that makes a leaked ingest key
-- harmless (`11` §3): the credential and the role it maps to are both
-- write-only.
grant insert on raw_events to rt_ingest;

-- Resolving `Authorization: Bearer rt_live_…` to a project requires reading
-- api_keys by hash. Deliberately the whole table rather than a definer function
-- that could bypass RLS: a hash is not a credential, so a role that can read
-- every hash still cannot forge a single key, and the alternative would
-- reintroduce exactly the bypass ADR-009 removed.
grant select on api_keys to rt_ingest;

-- Confirming the project exists and is not deleted.
grant select on projects to rt_ingest;

-- ── Policies ────────────────────────────────────────────────────────────────

-- The whole point. `with check` on INSERT is what pins the row to the
-- authenticated project; there is no `using` clause because rt_ingest may not
-- read raw_events at all.
create policy ingest_write on raw_events for insert to rt_ingest
  with check (project_id = rt_auth.current_project());

-- api_keys is FORCE RLS, so a grant alone reads nothing. Scoped to the role
-- rather than left open: `authenticated` still sees only its own project's keys
-- through the existing policy.
create policy ingest_reads_keys on api_keys for select to rt_ingest
  using (true);

-- Restricted to the project being authenticated, not open. rt_ingest never
-- needs to see another tenant's project row, and "it only reads one row" is a
-- property worth having the database enforce.
create policy ingest_reads_project on projects for select to rt_ingest
  using (id = rt_auth.current_project());

-- ── Partitions ──────────────────────────────────────────────────────────────
--
-- B13 again, and for the same reason: policies and grants do not propagate from
-- a partitioned parent to its partitions, and an INSERT routed to a partition
-- is checked against that partition's own policies. Without this, ingest would
-- fail on every insert — or, far worse, a future partition created without it
-- would fail only after the month rolled over.
--
-- Added inside `secure_partition` so there is still no code path that produces
-- a partition the ingest path cannot write to. `ensure_partitions` calls this
-- function, so next month's partition inherits the fix without anyone
-- remembering.
-- Now idempotent: every policy is dropped before it is created, so the
-- function can be re-run over partitions that already exist. That is what lets
-- this migration bring existing partitions up to the new policy set by calling
-- the same function the maintenance job calls, rather than by a parallel block
-- of DDL that could drift from it.
create or replace function rt_admin.secure_partition(p_table text) returns void
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  execute format('alter table public.%I enable row level security', p_table);
  execute format('alter table public.%I force  row level security', p_table);

  execute format('drop policy if exists tenant_read  on public.%I', p_table);
  execute format('drop policy if exists tenant_write on public.%I', p_table);
  execute format('drop policy if exists ingest_write on public.%I', p_table);

  -- Identical to the parent's policies, including the `to authenticated`
  -- scoping above, so the two access paths cannot diverge in behaviour.
  execute format($f$
    create policy tenant_read on public.%I for select to authenticated
      using (project_id in (select rt_auth.project_ids()))
  $f$, p_table);

  execute format($f$
    create policy tenant_write on public.%I for all to authenticated
      using      (project_id in (select rt_auth.project_ids())
                  and rt_auth.can_write_project(project_id))
      with check (project_id in (select rt_auth.project_ids())
                  and rt_auth.can_write_project(project_id))
  $f$, p_table);

  execute format('grant select on public.%I to authenticated', p_table);

  -- The ingest path. Only raw_events is written by S1; error_occurrences is
  -- worker-written, so it gets no ingest grant and rt_ingest cannot reach it.
  if p_table like 'raw\_events%' then
    execute format($f$
      create policy ingest_write on public.%I for insert to rt_ingest
        with check (project_id = rt_auth.current_project())
    $f$, p_table);
    execute format('grant insert on public.%I to rt_ingest', p_table);
  end if;
end $$;

revoke execute on function rt_admin.secure_partition(text) from public, anon, authenticated;

-- Apply to the partitions that already exist. `ensure_partitions` created them
-- before this migration ran, so they carry the old policy set.
do $$
declare
  part text;
begin
  for part in
    select c.relname
      from pg_class c
      join pg_inherits i on i.inhrelid = c.oid
      join pg_class p on p.oid = i.inhparent
     where p.relname in ('raw_events', 'error_occurrences')
  loop
    perform rt_admin.secure_partition(part);
  end loop;
end $$;
