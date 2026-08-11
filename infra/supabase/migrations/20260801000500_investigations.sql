-- 000500 — investigations, pipeline steps, LLM ledger.
--
-- docs/04-DATA-MODEL.md §8.

create table if not exists investigations (
  id                uuid primary key default public.uuid_generate_v7(),
  project_id        uuid not null references projects(id) on delete cascade,
  issue_id          uuid not null references issues(id)   on delete cascade,
  repository_id     uuid references repositories(id),
  trigger_occurrence_id uuid,                     -- soft reference (B3)

  status            investigation_status not null default 'queued',
  current_stage     pipeline_stage,
  triggered_by      text not null default 'auto',   -- auto | manual | replay
  triggered_by_user uuid references auth.users(id),
  replay_of         uuid references investigations(id),

  base_commit_sha   text,
  base_ref          text,

  confidence        numeric(4,3),
  confidence_band   confidence_band,

  repair_attempts   integer not null default 0,
  max_repair_attempts integer not null default 3,

  total_tokens_in   integer not null default 0,
  total_tokens_out  integer not null default 0,
  total_cost_micro_usd bigint not null default 0,
  total_duration_ms integer,

  terminal_reason   text,
  error_message     text,

  queued_at         timestamptz not null default now(),
  started_at        timestamptz,
  completed_at      timestamptz,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

-- Investigation history for a project.
create index if not exists investigations_created_idx on investigations (project_id, created_at desc);
-- Dashboard filter by status.
create index if not exists investigations_status_idx on investigations (project_id, status, created_at desc);
-- Investigations for one issue.
create index if not exists investigations_issue_idx on investigations (issue_id, created_at desc);
-- The live pipeline panel: what is running right now.
create index if not exists investigations_active_idx on investigations (project_id, status)
  where status in ('queued','analyzing','patching','validating','repairing',
                   'reviewing','scoring','publishing');

-- B8: the S3 investigation gate is a READ, so two occurrences of the same
-- fingerprint arriving concurrently would both pass it. This index is what
-- actually enforces "never two pipelines for one bug" — S3 catches the
-- unique_violation and attaches the occurrence to the existing run instead.
-- It is a cost control as much as a correctness one: without it an error storm
-- pays for two complete pipelines and opens two PRs for a single bug.
create unique index if not exists investigations_one_active_per_issue
  on investigations (issue_id)
  where status in ('queued','analyzing','patching','validating','repairing',
                   'reviewing','scoring','publishing');

-- The durability, idempotency, and audit backbone. The dashboard's pipeline
-- viewer is a direct render of these rows.
create table if not exists pipeline_steps (
  id                uuid primary key default public.uuid_generate_v7(),
  investigation_id  uuid not null references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id)       on delete cascade,

  stage             pipeline_stage not null,
  attempt           integer not null default 1,
  sequence          integer not null,
  status            step_status not null default 'queued',

  input_summary     jsonb,
  output_summary    jsonb,
  output_url        text,                          -- full payload in object storage
  output_hash       text,

  tokens_in         integer not null default 0,
  tokens_out        integer not null default 0,
  cost_micro_usd    bigint  not null default 0,

  started_at        timestamptz,
  completed_at      timestamptz,
  duration_ms       integer,

  error_code        text,
  error_message     text,
  error_detail      jsonb,

  created_at        timestamptz not null default now(),
  -- The idempotency guard: a redelivered job cannot double-insert (R2).
  unique (investigation_id, stage, attempt)
);
create index if not exists pipeline_steps_sequence_idx on pipeline_steps (investigation_id, sequence);

create table if not exists llm_calls (
  id                uuid primary key default public.uuid_generate_v7(),
  investigation_id  uuid references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,
  pipeline_step_id  uuid references pipeline_steps(id) on delete cascade,

  stage             pipeline_stage not null,
  tier              llm_tier not null,
  provider          text not null,
  model             text not null,
  prompt_version    text not null,

  prompt_url        text not null,                 -- object storage
  response_url      text not null,
  prompt_hash       text not null,                 -- enables deterministic caching

  tokens_in         integer not null,
  tokens_out        integer not null,
  cached_tokens_in  integer not null default 0,
  cost_micro_usd    bigint not null,

  latency_ms        integer not null,
  attempt           integer not null default 1,
  failover_from     text,
  schema_repair_used boolean not null default false,
  suspicious_content_detected boolean not null default false,

  created_at        timestamptz not null default now()
);
-- Cost analytics for a project.
create index if not exists llm_calls_project_idx on llm_calls (project_id, created_at desc);
-- Every call made by one investigation.
create index if not exists llm_calls_investigation_idx on llm_calls (investigation_id);
-- Per-stage, per-model cost breakdown.
create index if not exists llm_calls_model_idx on llm_calls (project_id, stage, model, created_at desc);
-- Deterministic-stage cache lookup.
create index if not exists llm_calls_prompt_hash_idx on llm_calls (prompt_hash);
