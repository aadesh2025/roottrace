-- 001500 — the coverage assertions. LAST, and that placement is the point.
--
-- docs/04-DATA-MODEL.md §12.9.
--
-- These assert the FINISHED state, so any migration that adds a relation without
-- securing it fails the run — including migrations written months from now by
-- someone who has never read this file. Running them earlier would fire on
-- tables that are legitimately not yet secured.
--
-- They fail the MIGRATION, not a test. The mistake cannot even reach a test run.

-- ── (1) Every relation holding tenant data has forced RLS ───────────────────
-- relkind 'p' = partitioned parent, 'r' = ordinary table AND every partition.
-- Partitions are deliberately IN SCOPE — see 001200 (B13).
--
-- There is no exemption list. One previously existed for `schema_migrations`,
-- naming a table that never appears in `public` (the Supabase CLI keeps its
-- history in the `supabase_migrations` schema). A standing exemption for a
-- hypothetical table is fail-open: it silently covers whatever later happens to
-- match. If a legitimate non-tenant table ever lands in `public`, this fires and
-- we decide deliberately.
do $$
declare missing text;
begin
  select string_agg(c.relname, ', ' order by c.relname) into missing
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind in ('r','p')
     and (not c.relrowsecurity or not c.relforcerowsecurity);
  if missing is not null then
    raise exception 'RLS missing or not forced on: %', missing;
  end if;
end $$;

-- ── (2) Every RLS-enabled relation has at least one policy ──────────────────
-- Enabled-with-no-policy is default-deny: it satisfies assertion (1) while
-- silently returning zero rows to every caller, including legitimate ones. That
-- failure mode is invisible until a query mysteriously returns nothing — and it
-- would make an isolation test pass while proving nothing.
do $$
declare missing text;
begin
  select string_agg(c.relname, ', ' order by c.relname) into missing
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind in ('r','p')
     and c.relrowsecurity
     and not exists (select 1 from pg_policy p where p.polrelid = c.oid);
  if missing is not null then
    raise exception 'RLS enabled but no policy on: %', missing;
  end if;
end $$;

-- ── (3) The 26 count is real, not aspirational ──────────────────────────────
-- docs/18 §6 fixes the logical table count at 26: 20 through the generic loop
-- plus 6 bespoke. This asserts that the number of RLS-protected NON-PARTITION
-- relations in public is exactly that, so a new table added without a policy —
-- or an old one quietly dropped from the loop array — fails the migration
-- rather than drifting away from the documented figure.
do $$
declare n int;
begin
  select count(*) into n
    from pg_class c
    join pg_namespace ns on ns.oid = c.relnamespace
   where ns.nspname = 'public'
     and c.relkind in ('r','p')
     and c.relrowsecurity
     and not exists (select 1 from pg_inherits i where i.inhrelid = c.oid);
  if n <> 26 then
    raise exception
      'expected 26 RLS-protected logical tables, found %. Update docs/18 §6 and '
      'the generic loop in 001000 together, or add the missing policy.', n;
  end if;
end $$;
