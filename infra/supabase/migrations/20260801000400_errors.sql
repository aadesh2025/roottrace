-- 000400 — errors: raw events, issues, occurrences.
--
-- docs/04-DATA-MODEL.md §7.
--
-- ┌─ NO PARTITIONS ARE CREATED HERE ──────────────────────────────────────────┐
-- │ `04` §7 originally created raw_events_2026_08 inline and called           │
-- │ rt_admin.secure_partition() on it. That cannot execute at this point:     │
-- │ secure_partition() is defined in 001200, and the policies it creates      │
-- │ reference rt_auth.project_ids(), defined in 000900. Migration 5 would     │
-- │ depend on migrations 10 and 13.                                           │
-- │                                                                           │
-- │ Partitions are created instead by rt_admin.ensure_partitions() in 001400. │
-- │ The deciding argument is not ordering convenience — it is that inline DDL │
-- │ here would be a SECOND path that creates partitions, running exactly once │
-- │ and never exercised again, while the maintenance function runs monthly.   │
-- │ That second path is precisely where B13 reopens. One path, exercised by   │
-- │ every `supabase db reset`.                                                │
-- └───────────────────────────────────────────────────────────────────────────┘

create table if not exists raw_events (
  id            uuid not null default public.uuid_generate_v7(),
  project_id    uuid not null references projects(id) on delete cascade,
  api_key_id    uuid references api_keys(id),
  batch_id      uuid not null,

  received_at   timestamptz not null default now(),
  event_ts      timestamptz not null,
  environment   environment_kind not null,
  service       text,
  release       text,

  payload       jsonb not null,                    -- sanitised (docs/03 §S1)
  payload_url   text,                              -- full original in object storage
  payload_bytes integer not null,
  redactions    jsonb not null default '[]',       -- what was redacted, never what it was

  is_valid      boolean not null default true,
  validation_errors jsonb,
  processed_at  timestamptz,

  -- PostgreSQL requires every unique/PK constraint on a partitioned table to
  -- include all partitioning columns (B3). The consequence is load-bearing:
  -- `id` alone is not unique-constrained, so NO other table may declare a
  -- foreign key to raw_events(id). error_occurrences.raw_event_id is therefore
  -- a documented soft reference, asserted by test rather than by the database.
  primary key (id, received_at)
) partition by range (received_at);

-- Indexes on the partitioned parent propagate automatically to every partition,
-- including ones created later by the maintenance job.
create index if not exists raw_events_project_idx on raw_events (project_id, received_at desc);
create index if not exists raw_events_batch_idx   on raw_events (batch_id);
create index if not exists raw_events_payload_idx on raw_events using gin (payload jsonb_path_ops);

create table if not exists issues (
  id                  uuid primary key default public.uuid_generate_v7(),
  project_id          uuid not null references projects(id) on delete cascade,

  fingerprint         text not null,
  error_type          text not null,
  normalized_message  text not null,
  sample_message      text not null,
  culprit             text,                        -- "services/checkout.py::calculate_total"
  route_pattern       text,

  status              issue_status not null default 'open',
  severity            severity_level not null default 'P2',
  severity_score      numeric(4,3),
  severity_factors    jsonb,

  -- Denormalised counters, maintained by upsert. COUNT(*) over millions of
  -- occurrence rows is not a dashboard query (§1).
  occurrence_count    bigint not null default 1,
  first_seen          timestamptz not null,
  last_seen           timestamptz not null,
  rate_per_hour       numeric(10,2) not null default 0,
  affected_user_count integer not null default 0,
  environments        environment_kind[] not null default '{}',
  affected_releases   text[] not null default '{}',
  affected_services   text[] not null default '{}',

  is_regression       boolean not null default false,
  regressed_from      uuid references issues(id),
  resolved_at         timestamptz,
  resolved_by         uuid references auth.users(id),
  muted_until         timestamptz,

  last_investigated_at timestamptz,
  investigation_count  integer not null default 0,

  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  -- The fingerprint upsert target. Also what makes 100 concurrent identical
  -- inserts produce exactly one issue (docs/14 §4.1).
  unique (project_id, fingerprint)
);

-- Issue list, default sort and filter.
create index if not exists issues_status_idx on issues (project_id, status, severity, last_seen desc);
-- Issue list, "recent" sort.
create index if not exists issues_last_seen_idx on issues (project_id, last_seen desc);
-- Analytics: top issues by volume.
create index if not exists issues_volume_idx on issues (project_id, occurrence_count desc);
-- Free-text issue search on the normalised message.
create index if not exists issues_message_trgm on issues
  using gin (normalized_message extensions.gin_trgm_ops);

create table if not exists error_occurrences (
  id            uuid not null default public.uuid_generate_v7(),
  project_id    uuid not null references projects(id) on delete cascade,
  issue_id      uuid not null references issues(id)   on delete cascade,
  raw_event_id  uuid not null,                        -- soft reference; see B3 above

  -- NOTE: occurred_at is the CUSTOMER's timestamp, not ours. It is the partition
  -- key, and S1 accepts events up to 7 days old (RT-INGEST-0012), so on the 1st
  -- to 7th of any month a perfectly valid event carries last month's date. See
  -- ensure_partitions(p_months_behind) in 001200.
  occurred_at   timestamptz not null,
  environment   environment_kind not null,
  service       text,
  release       text,
  user_hash     text,
  route_pattern text,
  status_code   integer,
  duration_ms   integer,
  stack_frames  jsonb,
  breadcrumbs   jsonb,
  tags          jsonb not null default '{}',

  primary key (id, occurred_at)                       -- B3, as above
) partition by range (occurred_at);

-- Issue detail: the occurrence chart.
create index if not exists error_occurrences_issue_idx on error_occurrences (issue_id, occurred_at desc);
-- Project-wide error volume.
create index if not exists error_occurrences_project_idx on error_occurrences (project_id, occurred_at desc);
-- Soft-reference lookups back to raw_events (no FK exists to serve them).
create index if not exists error_occurrences_raw_idx on error_occurrences (raw_event_id);
