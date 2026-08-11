-- 000000 — extensions, UUIDv7, and every enum.
--
-- docs/04-DATA-MODEL.md §3. Runs first: everything below depends on the types
-- declared here.
--
-- Extensions land in the `extensions` schema, which is Supabase's convention and
-- also keeps `public` clean — the §12.9 coverage assertion scans `public` for
-- relkind 'r'/'p', so an extension that created a table there would trip it.
-- None of these four create tables, but the habit is worth keeping.

create schema if not exists extensions;

create extension if not exists "pgcrypto"  with schema extensions;
create extension if not exists "vector"    with schema extensions;  -- pgvector
create extension if not exists "pg_trgm"   with schema extensions;  -- trigram search
create extension if not exists "btree_gin" with schema extensions;

-- UUIDv7: time-sortable, index-friendly. Volatile by definition — it reads the
-- clock — which is correct for a column default.
create or replace function public.uuid_generate_v7() returns uuid
language sql volatile
set search_path = pg_catalog, extensions, public
as $$
  select encode(
    set_bit(set_bit(overlay(uuid_send(gen_random_uuid())
      placing substring(int8send(floor(extract(epoch from clock_timestamp()) * 1000)::bigint)
              from 3) from 1 for 6), 52, 1), 53, 1), 'hex')::uuid;
$$;

-- ── Enums ────────────────────────────────────────────────────────────────────
--
-- Explicit enums, not free text: an invalid state is impossible rather than
-- merely unlikely (§1). `create type` has no `if not exists`, so each is guarded
-- to keep the migration re-runnable (§15).

do $$ begin
  create type project_plan as enum ('free','pro','team','enterprise');
exception when duplicate_object then null; end $$;

do $$ begin
  create type member_role as enum ('owner','maintainer','viewer');
exception when duplicate_object then null; end $$;

do $$ begin
  create type environment_kind as enum ('production','staging','development','test');
exception when duplicate_object then null; end $$;

do $$ begin
  create type severity_level as enum ('P0','P1','P2','P3');
exception when duplicate_object then null; end $$;

do $$ begin
  create type issue_status as enum
    ('open','investigating','resolved','regressed','muted','ignored');
exception when duplicate_object then null; end $$;

do $$ begin
  create type investigation_status as enum (
    'queued','analyzing','patching','validating','repairing','reviewing','scoring',
    'publishing','awaiting_decision',
    'merged','edited_and_merged','rejected','stale',
    'insufficient_context','validation_failed','low_confidence','failed','cancelled'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type pipeline_stage as enum (
    'receive','fingerprint','triage','understand','retrieve','reason','patch',
    'validate','repair','critique','score','publish','await_decision','feedback'
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type step_status as enum
    ('queued','running','completed','failed','skipped','terminal','deferred');
exception when duplicate_object then null; end $$;

do $$ begin
  create type confidence_band as enum ('high','medium','low','insufficient');
exception when duplicate_object then null; end $$;

do $$ begin
  create type critic_verdict as enum
    ('approve','approve_with_notes','request_changes','reject');
exception when duplicate_object then null; end $$;

do $$ begin
  create type validation_mode as enum ('full','partial','syntax_only');
exception when duplicate_object then null; end $$;

do $$ begin
  create type feedback_outcome as enum (
    'merged_unchanged','edited_and_merged','rejected','stale','human_took_over','cancelled');
exception when duplicate_object then null; end $$;

do $$ begin
  create type code_node_kind as enum
    ('function','method','class','module','interface','type');
exception when duplicate_object then null; end $$;

do $$ begin
  create type code_edge_kind as enum
    ('calls','imports','extends','implements','references');
exception when duplicate_object then null; end $$;

do $$ begin
  create type index_status as enum ('pending','indexing','ready','failed','disabled');
exception when duplicate_object then null; end $$;

do $$ begin
  create type llm_tier as enum ('fast','reasoning_a','reasoning_b','embed');
exception when duplicate_object then null; end $$;
