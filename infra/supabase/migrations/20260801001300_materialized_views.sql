-- 001300 — dashboard matviews, revoked, and their scoped accessors.
--
-- docs/04-DATA-MODEL.md §13, §13.1 (B6).
--
-- PostgreSQL does not support row-level security on a materialised view, and a
-- matview does NOT inherit the policies of the tables it was built from. Both
-- views below contain per-project rows, so direct access by `authenticated`
-- would be an unrestricted cross-tenant aggregate leak — one tenant reading
-- another's error volumes, costs, and health scores.
--
-- This is enforced, not assumed: the views are revoked outright and every read
-- goes through a SECURITY DEFINER accessor that re-applies tenant scoping
-- INSIDE the function, where the caller cannot remove it.

create materialized view if not exists issue_hourly_counts as
select issue_id, project_id,
       date_trunc('hour', occurred_at) as hour,
       count(*) as occurrences,
       count(distinct user_hash) as affected_users
from error_occurrences
where occurred_at > now() - interval '30 days'
group by 1,2,3;

-- Required for REFRESH MATERIALIZED VIEW CONCURRENTLY (every 5 minutes).
create unique index if not exists issue_hourly_counts_pk
  on issue_hourly_counts (issue_id, hour);

create materialized view if not exists project_health_daily as
select p.id as project_id,
       d.day,
       coalesce(u.events_ingested, 0)          as events,
       count(distinct i.id) filter (where i.created_at::date = d.day) as new_issues,
       count(distinct inv.id) filter (where inv.status in ('merged','edited_and_merged')) as fixes_merged,
       avg(inv.confidence) filter (where inv.confidence is not null) as avg_confidence,
       coalesce(u.llm_cost_micro_usd, 0)       as cost_micro_usd
from projects p
cross join generate_series(current_date - 89, current_date, '1 day') d(day)
left join usage_daily u   on u.project_id = p.id and u.day = d.day
left join issues i        on i.project_id = p.id
left join investigations inv on inv.project_id = p.id and inv.created_at::date = d.day
group by p.id, d.day, u.events_ingested, u.llm_cost_micro_usd;

create unique index if not exists project_health_daily_pk
  on project_health_daily (project_id, day);

-- The isolation. Note these run AFTER the matviews exist, and are the only
-- reason a matview is not a hole.
revoke all on issue_hourly_counts  from anon, authenticated;
revoke all on project_health_daily from anon, authenticated;

create or replace function public.issue_hourly_counts_for(
  p_issue_id uuid, p_since timestamptz, p_until timestamptz)
returns table (hour timestamptz, occurrences bigint, affected_users bigint)
language sql stable security definer
set search_path = pg_catalog, public
as $$
  select h.hour, h.occurrences, h.affected_users
    from issue_hourly_counts h
   where h.issue_id = p_issue_id
     and h.hour >= p_since and h.hour < p_until
     and h.project_id in (select rt_auth.project_ids())   -- ← the isolation
$$;

create or replace function public.project_health_daily_for(
  p_project_id uuid, p_since date, p_until date)
returns table (day date, events bigint, new_issues bigint,
               fixes_merged bigint, avg_confidence numeric, cost_micro_usd bigint)
language sql stable security definer
set search_path = pg_catalog, public
as $$
  select d.day, d.events, d.new_issues, d.fixes_merged, d.avg_confidence, d.cost_micro_usd
    from project_health_daily d
   where d.project_id = p_project_id
     and d.day >= p_since and d.day < p_until
     and d.project_id in (select rt_auth.project_ids())   -- ← the isolation
$$;

-- These two remain SECURITY DEFINER and keep the migration role as owner. That
-- is NOT the BYPASSRLS pattern the helpers used to need: a matview carries no
-- RLS at all (B6 is precisely that it cannot), so the accessor needs only the
-- SELECT PRIVILEGE that `authenticated` was revoked. It bypasses no policy.
--
-- The tenant filter inside still resolves correctly, because rt_auth.uid() reads
-- the request JWT rather than the executing role — a definer context does not
-- change whose token is being presented.

revoke execute on function public.issue_hourly_counts_for(uuid, timestamptz, timestamptz)
  from public;
revoke execute on function public.project_health_daily_for(uuid, date, date)
  from public;
grant execute on function public.issue_hourly_counts_for(uuid, timestamptz, timestamptz)
  to authenticated;
grant execute on function public.project_health_daily_for(uuid, date, date)
  to authenticated;
