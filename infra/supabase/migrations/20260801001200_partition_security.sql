-- 001200 — rt_admin.secure_partition() and rt_admin.ensure_partitions().
--
-- docs/04-DATA-MODEL.md §12.10 (B13).
--
-- ═══════════════════════════════════════════════════════════════════════════
-- THE MOST EASILY MISSED HOLE IN THE TENANCY MODEL
-- ═══════════════════════════════════════════════════════════════════════════
-- Everything about it looks correct until someone names a partition directly.
--
--   select * from raw_events            → the PARENT's policies apply. Correct.
--   select * from raw_events_2026_08    → ONLY that partition's own policies.
--                                          The parent's are never consulted.
--
-- `alter table raw_events enable row level security` sets the flag on the parent
-- alone. Partitions are independent relations with independent flags, and
-- `authenticated` can name one directly. A partition created without RLS is a
-- complete tenant-isolation bypass for anyone who knows the naming convention.
--
-- We do NOT exclude partitions from the §12.9 coverage assertion. Excluding them
-- would make the migration pass while leaving the hole open. The assertion
-- firing is the system working.

create schema if not exists rt_admin;
revoke all on schema rt_admin from public, anon, authenticated;

-- Applies the standard project-scoped policy set, forced RLS, and the Data API
-- grants to ONE partition.
--
-- Deliberately NOT security definer: it must not be callable by anyone who is
-- not already entitled to create policies.
create or replace function rt_admin.secure_partition(p_table text) returns void
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  execute format('alter table public.%I enable row level security', p_table);
  execute format('alter table public.%I force  row level security', p_table);

  -- Identical to the parent's policies (001000), so the two access paths cannot
  -- diverge in behaviour — only in which relation enforces them.
  execute format($f$
    create policy tenant_read on public.%I for select
      using (project_id in (select rt_auth.project_ids()))
  $f$, p_table);

  execute format($f$
    create policy tenant_write on public.%I for all
      using      (project_id in (select rt_auth.project_ids())
                  and rt_auth.can_write_project(project_id))
      with check (project_id in (select rt_auth.project_ids())
                  and rt_auth.can_write_project(project_id))
  $f$, p_table);

  -- Grants do not propagate from parent to partition any more than policies do,
  -- and privilege is checked against the relation actually named in the query.
  -- Without this, a direct partition read fails with `permission denied` — which
  -- looks like isolation but is not, and would make the B13 regression test pass
  -- for the wrong reason (see the grants note in 001000).
  execute format('grant select on public.%I to authenticated', p_table);
end $$;

revoke execute on function rt_admin.secure_partition(text) from public, anon, authenticated;

-- Creates and secures monthly partitions in ONE function. There is deliberately
-- no code path that produces an unsecured partition: a partition created without
-- the securing call reopens the hole silently, on a monthly cadence, with no
-- code change for anyone to review.
--
-- p_months_behind exists because `error_occurrences` is partitioned on
-- `occurred_at` — the CUSTOMER's timestamp, not ours — and S1 accepts events up
-- to 7 days old (RT-INGEST-0012). On the 1st to 7th of any month a perfectly
-- valid event carries last month's date. Current-month-forward only would make
-- that insert fail with "no partition of relation found for row": intermittent,
-- only in the first week of a month, and therefore the worst possible failure
-- signature.
--
-- A DEFAULT partition would also prevent the error, and is deliberately NOT used:
-- rows would land in it silently and detaching it later is painful. That trades
-- a loud failure for a quiet one.
create or replace function rt_admin.ensure_partitions(
  p_months_ahead int default 3,
  p_months_behind int default 1
) returns void
language plpgsql
set search_path = pg_catalog, public
as $$
declare
  target date; part text; parent text;
begin
  foreach parent in array array['raw_events','error_occurrences'] loop
    for i in -p_months_behind..p_months_ahead loop
      target := date_trunc('month', current_date) + (i || ' months')::interval;
      part   := parent || '_' || to_char(target, 'YYYY_MM');

      if to_regclass('public.' || part) is null then
        execute format(
          'create table public.%I partition of public.%I for values from (%L) to (%L)',
          part, parent, target, target + interval '1 month');

        -- NON-NEGOTIABLE: a partition is not usable until it is secured.
        perform rt_admin.secure_partition(part);
      end if;
    end loop;
  end loop;
end $$;

revoke execute on function rt_admin.ensure_partitions(int, int) from public, anon, authenticated;
