-- 001400 — create the initial partitions, and schedule the monthly job.
--
-- docs/04-DATA-MODEL.md §15.
--
-- This is the ONLY place partitions are ever created. `04` §7 originally created
-- the first ones inline in 000400 with a separate secure_partition() call; that
-- could not execute there (secure_partition is defined in 001200 and its
-- policies reference rt_auth, defined in 000900), but the deciding argument is
-- not ordering. Inline DDL would be a second creation path that runs exactly
-- once and is never exercised again, while this one runs every month. The path
-- that never runs again is precisely where B13 reopens.
--
-- One path, exercised by every `supabase db reset`.

-- Current month, one behind (late-arriving events, see 001200), three ahead.
select rt_admin.ensure_partitions(3, 1);

-- The monthly job. pg_cron is available on Supabase but is not installed by the
-- local CLI stack by default, so scheduling is conditional: if the extension is
-- present we schedule, and if it is not we say so loudly rather than silently
-- leaving the system with no partition maintenance.
--
-- The worker also calls ensure_partitions() on boot (T2.1), so partition
-- creation does not depend on cron being present in any environment.
do $$
begin
  if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
    create extension if not exists pg_cron;

    perform cron.unschedule('roottrace_ensure_partitions')
      where exists (select 1 from cron.job where jobname = 'roottrace_ensure_partitions');

    perform cron.schedule(
      'roottrace_ensure_partitions',
      '0 3 1 * *',                       -- 03:00 UTC on the 1st of each month
      $cron$select rt_admin.ensure_partitions(3, 1)$cron$
    );
    raise notice 'partition maintenance scheduled via pg_cron';
  else
    raise notice
      'pg_cron unavailable: partition maintenance is NOT scheduled in this database. '
      'The worker calls rt_admin.ensure_partitions() on boot, which covers local '
      'and CI. Staging and production must confirm pg_cron is enabled.';
  end if;
end $$;
