-- 000600 — pipeline artefacts.
--
-- docs/04-DATA-MODEL.md §9. Large payloads (source, diffs, transcripts, prompts)
-- live in object storage; these tables hold the URL and a hash (§1).

create table if not exists context_bundles (
  id                uuid primary key default public.uuid_generate_v7(),
  investigation_id  uuid not null unique references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,

  repository_id     uuid references repositories(id),
  commit_sha        text,
  ref               text,

  -- token_budget is the hard 24,000 ceiling from P3. token_count must never
  -- exceed it; that invariant is enforced in the ranking code and tested across
  -- all 25 fixtures (T4.4).
  token_count       integer not null,
  token_budget      integer not null,
  file_count        integer not null,

  files             jsonb not null,     -- [{repo_path, strategy, relevance, line_range, ...}]
  content_url       text not null,      -- actual source in object storage
  graph             jsonb not null,     -- {nodes:[], edges:[]}
  history           jsonb not null,     -- blame, recent commits, release diff, PRs
  tests             jsonb not null,
  strategy_stats    jsonb not null,
  quality_score     numeric(4,3) not null,
  quality_signals   jsonb not null,
  gaps              text[] not null default '{}',

  created_at        timestamptz not null default now()
);

create table if not exists root_cause_analyses (
  id                uuid primary key default public.uuid_generate_v7(),
  investigation_id  uuid not null unique references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,

  summary           text not null,
  mechanism         text not null,
  category          text not null,
  introduced_by_sha text,
  introduced_by_author text,
  introduced_by_date timestamptz,

  blast_radius      jsonb not null,
  -- The full reasoning chain WITH its evidence. P2: every claim carries its
  -- binding, and unbound claims are discarded before they reach the UI.
  reasoning_chain   jsonb not null,
  eliminated_hypotheses jsonb not null default '[]',
  fix_strategy      jsonb not null,

  self_assessed_confidence numeric(4,3),
  uncertainty_notes text[],
  evidence_validation jsonb not null,   -- which findings passed/failed binding checks

  model             text not null,
  prompt_version    text not null,
  created_at        timestamptz not null default now()
);

create table if not exists patches (
  id                uuid primary key default public.uuid_generate_v7(),
  investigation_id  uuid not null references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,

  attempt           integer not null default 1,
  base_commit       text not null,
  diff_url          text not null,                 -- object storage
  diff_hash         text not null,
  files_changed     jsonb not null,
  total_additions   integer not null,
  total_deletions   integer not null,

  explanation       text not null,
  regression_test   jsonb,
  risk_assessment   jsonb not null,
  alternatives_considered jsonb not null default '[]',
  scope_warning     text,

  model             text not null,
  prompt_version    text not null,
  created_at        timestamptz not null default now(),
  unique (investigation_id, attempt)
);

create table if not exists validation_runs (
  id                uuid primary key default public.uuid_generate_v7(),
  investigation_id  uuid not null references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,
  patch_id          uuid not null references patches(id) on delete cascade,

  attempt           integer not null default 1,
  passed            boolean not null,
  -- `partial` and `syntax_only` are reported honestly and cap the validation
  -- component of confidence. A degraded run must never look like a clean pass
  -- (T6.5).
  mode              validation_mode not null default 'full',
  failed_gate       text,

  gates             jsonb not null,                -- per-gate results, G0-G8
  failure_detail    jsonb,
  repair_hint       text,
  signals_for_scoring jsonb not null,

  transcript_url    text,
  transcript_bytes  integer,
  transcript_truncated boolean not null default false,

  wall_ms           integer not null,
  cpu_ms            integer,
  peak_memory_mb    integer,
  container_image   text not null,

  created_at        timestamptz not null default now(),
  unique (investigation_id, attempt)
);

create table if not exists critiques (
  id                uuid primary key default public.uuid_generate_v7(),
  investigation_id  uuid not null unique references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,
  patch_id          uuid not null references patches(id) on delete cascade,

  verdict           critic_verdict not null,
  agreement_with_diagnosis numeric(4,3) not null,
  addresses_reported_error boolean not null,
  findings          jsonb not null default '[]',
  security_review   jsonb not null,
  regression_risk   text not null,
  test_quality      jsonb not null,
  scope_assessment  text,
  blocking          boolean not null default false,

  model             text not null,                 -- a DIFFERENT provider where
  prompt_version    text not null,                 -- available: independence is the point
  created_at        timestamptz not null default now()
);

create table if not exists confidence_scores (
  id                uuid primary key default public.uuid_generate_v7(),
  investigation_id  uuid not null unique references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,

  confidence        numeric(4,3) not null,
  band              confidence_band not null,
  breakdown         jsonb not null,                -- per-component weight/raw/contribution
  gates_applied     text[] not null default '{}',
  explanation       text not null,
  should_publish    boolean not null,
  publish_mode      text not null,
  auto_merge_eligible boolean not null default false,

  formula_version   text not null default 'v1',
  created_at        timestamptz not null default now()
);
