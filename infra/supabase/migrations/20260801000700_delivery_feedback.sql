-- 000700 — delivery and feedback.
--
-- docs/04-DATA-MODEL.md §10.

create table if not exists pull_request_records (
  id                uuid primary key default public.uuid_generate_v7(),
  investigation_id  uuid not null unique references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,
  repository_id     uuid references repositories(id),

  -- TRUE for every row V1 ever writes. GITHUB_MODE=fixture throughout, and the
  -- evaluation tier cannot mint an installation token, so no real PR is
  -- reachable. V2 flips a config value, not code.
  is_simulated      boolean not null default false,
  github_pr_number  integer,
  github_pr_id      bigint,
  url               text,
  branch_name       text not null,
  commit_sha        text,
  base_sha          text not null,

  title             text not null,
  body              text not null,                 -- full rendered markdown (docs/03 §S12)
  is_draft          boolean not null default false,
  labels            text[] not null default '{}',

  state             text not null default 'open',  -- open|merged|closed
  merged_at         timestamptz,
  merged_by         text,
  closed_at         timestamptz,
  human_commits_added integer not null default 0,

  ci_status         text,                          -- V2: the second gate
  ci_checked_at     timestamptz,

  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create table if not exists feedback_events (
  id                uuid primary key default public.uuid_generate_v7(),
  investigation_id  uuid not null references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,

  outcome           feedback_outcome not null,
  decided_at        timestamptz not null,
  decided_by        text,
  time_to_decision_seconds integer,

  human_edit_diff_url text,
  edit_analysis     jsonb,
  semantic_verdict  text,
  signal_strength   numeric(4,3),
  learning_targets  text[] not null default '{}',
  user_comment      text,

  created_at        timestamptz not null default now()
);
-- Calibration analytics: outcomes over time for a project.
create index if not exists feedback_events_outcome_idx
  on feedback_events (project_id, outcome, created_at desc);

-- V4 chat. Schema present from V1 so that enabling it later needs no migration
-- against a live system; RT_FF_INVESTIGATION_CHAT stays false until then.
create table if not exists investigation_messages (
  id                uuid primary key default public.uuid_generate_v7(),
  investigation_id  uuid not null references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,
  user_id           uuid references auth.users(id),

  role              text not null check (role in ('user','assistant','system')),
  content           text not null,
  citations         jsonb not null default '[]',
  tokens_in         integer,
  tokens_out        integer,
  cost_micro_usd    bigint,
  model             text,
  created_at        timestamptz not null default now()
);
create index if not exists investigation_messages_idx
  on investigation_messages (investigation_id, created_at);
