-- 001100 — the last-owner invariant.
--
-- docs/04-DATA-MODEL.md §12.5.
--
-- RLS cannot express "an organization must retain at least one owner": that is a
-- cardinality rule over the table, not a predicate on a row. Policies decide
-- whether THIS row may be written; they cannot see what would remain afterwards.
-- Hence a trigger.

create or replace function rt_auth.assert_owner_remains() returns trigger
language plpgsql security definer
set search_path = pg_catalog, public
as $$
begin
  if tg_table_name = 'organization_members' then
    if not exists (select 1 from public.organization_members
                    where organization_id = old.organization_id
                      and role = 'owner'
                      and user_id <> old.user_id) then
      raise exception 'RT-AUTH-0030: organization must retain at least one owner';
    end if;
  else
    if not exists (select 1 from public.project_members
                    where project_id = old.project_id
                      and role = 'owner'
                      and user_id <> old.user_id) then
      raise exception 'RT-AUTH-0030: project must retain at least one owner';
    end if;
  end if;
  return old;
end $$;

-- SECURITY DEFINER and owned by the migration role, which is not subject to the
-- own-row-only policy. The trigger must count OTHER owners to know one remains,
-- which is exactly the read the policy forbids — a cardinality rule cannot be
-- answered from a single row. This is the one place the invariant needs a wider
-- view than any user has, and it returns a boolean, never rows.

-- 000900's blanket `revoke execute on all functions in schema rt_auth from
-- public` ran BEFORE this function existed, so it would otherwise keep the
-- default PUBLIC execute grant — leaving a SECURITY DEFINER function that reads
-- the membership tables callable by `anon`. Blanket revokes only cover what is
-- already there; anything added later must revoke for itself.
revoke execute on function rt_auth.assert_owner_remains() from public, anon, authenticated;

drop trigger if exists org_members_keep_owner on organization_members;
create trigger org_members_keep_owner
  before delete or update on organization_members
  for each row execute function rt_auth.assert_owner_remains();

drop trigger if exists project_members_keep_owner on project_members;
create trigger project_members_keep_owner
  before delete or update on project_members
  for each row execute function rt_auth.assert_owner_remains();
