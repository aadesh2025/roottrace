# 04 — Data Model

> Complete Supabase/Postgres schema: DDL, indexes, row-level security, retention, and the reasoning behind each modelling decision.

---

## 1. Modelling principles

| Principle | Application |
|---|---|
| **Tenant isolation at the database layer** | Every tenant table has `project_id` + an RLS policy. A forgotten `WHERE` clause cannot leak data |
| **Immutable history** | `raw_events`, `error_occurrences`, `pipeline_steps`, `llm_calls`, `audit_log` are append-only. Never updated, never deleted outside retention |
| **Normalise identity, denormalise counters** | Issues carry `occurrence_count`, `first_seen`, `last_seen` — maintained by upsert, not computed by `COUNT(*)` over millions of rows |
| **Large blobs live outside Postgres** | Full payloads, sandbox transcripts, prompts, and diffs go to object storage; the table holds a URL and a hash |
| **Explicit enums, not free text** | Every status and category is a Postgres enum. Invalid states are impossible, not merely unlikely |
| **Time-sortable IDs** | UUIDv7 everywhere — index locality and natural chronological ordering without a separate sequence |
| **Cost is data** | Tokens and micro-USD are first-class columns, not log lines |

---

## 2. Entity relationship overview

```
  auth.users (Supabase GoTrue)
        │
        │ 1:N
        ▼
  ┌───────────────┐        ┌────────────────────┐
  │ organizations │───N:M──│  organization_members │
  └───────┬───────┘        └────────────────────┘
          │ 1:N
          ▼
  ┌───────────────┐        ┌──────────────────┐      ┌────────────┐
  │   projects    │───N:M──│ project_members  │      │  api_keys  │
  └───────┬───────┘        └──────────────────┘      └─────┬──────┘
          │                                                │
          │ 1:N                                            │ N:1
          ├────────────────────────────────────────────────┘
          │
          ├──► github_installations ──1:N──► repositories ──1:N──► code_nodes
          │                                        │                    │
          │                                        │              1:N   │
          │                                        │                    ▼
          │                                        └──1:N──────► code_edges
          │
          ├──► raw_events ──────────┐
          │                          │ N:1
          ├──► issues ◄──────────────┴──── error_occurrences
          │       │
          │       │ 1:N
          │       ▼
          └──► investigations
                    │
                    ├──1:N──► pipeline_steps
                    ├──1:N──► llm_calls
                    ├──1:1──► context_bundles
                    ├──1:1──► root_cause_analyses
                    ├──1:N──► patches            (one per attempt)
                    ├──1:N──► validation_runs    (one per attempt)
                    ├──1:1──► critiques
                    ├──1:1──► confidence_scores
                    ├──1:1──► pull_request_records
                    ├──1:N──► feedback_events
                    └──1:N──► investigation_messages   (V4 chat)
```

---

## 3. Extensions and enums

```sql
create extension if not exists "pgcrypto";
create extension if not exists "vector";       -- pgvector
create extension if not exists "pg_trgm";      -- trigram search on messages
create extension if not exists "btree_gin";

-- UUIDv7: time-sortable, index-friendly
create or replace function uuid_generate_v7() returns uuid as $$
  select encode(
    set_bit(set_bit(overlay(uuid_send(gen_random_uuid())
      placing substring(int8send(floor(extract(epoch from clock_timestamp()) * 1000)::bigint)
              from 3) from 1 for 6), 52, 1), 53, 1), 'hex')::uuid;
$$ language sql volatile;

create type project_plan          as enum ('free','pro','team','enterprise');
create type member_role           as enum ('owner','maintainer','viewer');
create type environment_kind      as enum ('production','staging','development','test');
create type severity_level        as enum ('P0','P1','P2','P3');
create type issue_status          as enum ('open','investigating','resolved','regressed','muted','ignored');

create type investigation_status  as enum (
  'queued','analyzing','patching','validating','repairing','reviewing','scoring',
  'publishing','awaiting_decision',
  'merged','edited_and_merged','rejected','stale',
  'insufficient_context','validation_failed','low_confidence','failed','cancelled'
);

create type pipeline_stage        as enum (
  'receive','fingerprint','triage','understand','retrieve','reason','patch',
  'validate','repair','critique','score','publish','await_decision','feedback'
);
create type step_status           as enum ('queued','running','completed','failed','skipped','terminal','deferred');
create type confidence_band       as enum ('high','medium','low','insufficient');
create type critic_verdict        as enum ('approve','approve_with_notes','request_changes','reject');
create type validation_mode       as enum ('full','partial','syntax_only');
create type feedback_outcome      as enum (
  'merged_unchanged','edited_and_merged','rejected','stale','human_took_over','cancelled');
create type code_node_kind        as enum ('function','method','class','module','interface','type');
create type code_edge_kind        as enum ('calls','imports','extends','implements','references');
create type index_status          as enum ('pending','indexing','ready','failed','disabled');
create type llm_tier              as enum ('fast','reasoning_a','reasoning_b','embed');
```

---

## 4. Identity and tenancy

```sql
create table organizations (
  id            uuid primary key default uuid_generate_v7(),
  name          text not null,
  slug          text not null unique check (slug ~ '^[a-z0-9-]{2,48}$'),
  plan          project_plan not null default 'free',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  deleted_at    timestamptz
);

create table organization_members (
  organization_id uuid not null references organizations(id) on delete cascade,
  user_id         uuid not null references auth.users(id)    on delete cascade,
  role            member_role not null default 'viewer',
  invited_by      uuid references auth.users(id),
  created_at      timestamptz not null default now(),
  primary key (organization_id, user_id)
);

create table projects (
  id                uuid primary key default uuid_generate_v7(),
  organization_id   uuid not null references organizations(id) on delete cascade,
  name              text not null,
  slug              text not null,
  description       text,

  -- behavioural configuration (documented in appendix A3)
  settings          jsonb not null default '{
    "min_investigation_severity": "P2",
    "investigated_environments": ["production"],
    "investigation_cooldown_hours": 6,
    "confidence_floor_for_pr": 0.40,
    "auto_merge_enabled": false,
    "auto_merge_paths": [],
    "auto_merge_min_confidence": 0.90,
    "endpoint_criticality": {},
    "fingerprint_rules": [],
    "path_mappings": [],
    "model_tier_overrides": {}
  }'::jsonb,

  -- cost control
  daily_cost_cap_micro_usd    bigint not null default 5000000,   -- $5.00
  monthly_cost_cap_micro_usd  bigint not null default 100000000, -- $100.00
  cost_breaker_open           boolean not null default false,
  cost_breaker_opened_at      timestamptz,

  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  deleted_at        timestamptz,
  unique (organization_id, slug)
);

create table project_members (
  project_id  uuid not null references projects(id)     on delete cascade,
  user_id     uuid not null references auth.users(id)   on delete cascade,
  role        member_role not null default 'viewer',
  created_at  timestamptz not null default now(),
  primary key (project_id, user_id)
);
create index on project_members (user_id);
```

### API keys

```sql
create table api_keys (
  id            uuid primary key default uuid_generate_v7(),
  project_id    uuid not null references projects(id) on delete cascade,
  name          text not null,
  key_prefix    text not null,                      -- "rt_live_a3f8" — shown in UI
  key_hash      text not null unique,               -- sha256 of the full key
  scopes        text[] not null default '{events:write}',
  environment   environment_kind not null default 'production',

  last_used_at  timestamptz,
  last_used_ip  inet,
  use_count     bigint not null default 0,

  created_by    uuid references auth.users(id),
  created_at    timestamptz not null default now(),
  expires_at    timestamptz,
  revoked_at    timestamptz,
  revoked_by    uuid references auth.users(id)
);
create index on api_keys (project_id) where revoked_at is null;
-- NOTE: no separate index on key_hash. The `unique` constraint on the column
-- already creates a btree index that serves the `key_hash = $1` equality lookup
-- on the ingest hot path. A partial index would never be chosen over it and
-- would only add write cost. See C8 in `18` §9.
```

> **The full key is never stored.** It is generated, hashed, returned once, and unrecoverable thereafter. `key_prefix` exists solely so the UI can identify which key is which. Lookup is by `key_hash`; comparison is constant-time in application code.

**Index justification** (per C8 — every retained index states the query it serves):

| Index | Serves |
|---|---|
| `unique (key_hash)` | Ingest auth: `where key_hash = $1 and revoked_at is null`. Constraint and hot-path index in one |
| `(project_id) where revoked_at is null` | Settings → API keys list for one project |

---

## 5. GitHub

```sql
create table github_installations (
  id                    uuid primary key default uuid_generate_v7(),
  organization_id       uuid not null references organizations(id) on delete cascade,
  installation_id       bigint not null unique,          -- GitHub's ID
  account_login         text not null,
  account_type          text not null,                   -- User | Organization
  repository_selection  text not null,                   -- all | selected
  permissions           jsonb not null,
  events                text[] not null,
  suspended_at          timestamptz,
  needs_reauth          boolean not null default false,
  created_at            timestamptz not null default now(),
  deleted_at            timestamptz
);

create table repositories (
  id                uuid primary key default uuid_generate_v7(),
  project_id        uuid not null references projects(id) on delete cascade,
  installation_id   uuid not null references github_installations(id) on delete cascade,

  github_repo_id    bigint not null,
  full_name         text not null,                   -- "acme/checkout-api"
  default_branch    text not null default 'main',
  primary_language  text,
  is_private        boolean not null default true,
  is_archived       boolean not null default false,

  root_path         text default '',                 -- monorepo subdirectory
  service_map       jsonb not null default '{}',     -- {"checkout-api": "services/checkout"}
  path_mappings     jsonb not null default '[]',     -- [{"from":"/app/","to":""}]

  index_status      index_status not null default 'pending',
  last_indexed_sha  text,
  last_indexed_at   timestamptz,
  indexed_node_count integer not null default 0,

  github_live_enabled boolean not null default false, -- V1 safety switch
  created_at        timestamptz not null default now(),
  deleted_at        timestamptz,
  unique (project_id, github_repo_id)
);
```

---

## 6. Code index (populated from V2; schema exists from V1)

```sql
create table code_nodes (
  id             uuid primary key default uuid_generate_v7(),
  project_id     uuid not null references projects(id)     on delete cascade,
  repository_id  uuid not null references repositories(id) on delete cascade,

  repo_path      text not null,
  symbol_name    text not null,                    -- "TaxClient.get_rate"
  kind           code_node_kind not null,
  language       text not null,
  signature      text,
  docstring      text,
  start_line     integer not null,
  end_line       integer not null,
  source         text not null,
  source_hash    text not null,                    -- skip re-embedding unchanged nodes

  embedding      vector(1536),
  embedding_model text,
  embedded_at    timestamptz,

  commit_sha     text not null,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (repository_id, repo_path, symbol_name, start_line)
);

create index code_nodes_repo_path_idx on code_nodes (repository_id, repo_path);
create index code_nodes_symbol_idx    on code_nodes (repository_id, symbol_name);
create index code_nodes_trgm_idx      on code_nodes using gin (symbol_name gin_trgm_ops);

-- HNSW: better recall/latency than IVFFlat and no retraining as data grows
create index code_nodes_embedding_idx on code_nodes
  using hnsw (embedding vector_cosine_ops) with (m = 16, ef_construction = 64);

create table code_edges (
  id             uuid primary key default uuid_generate_v7(),
  project_id     uuid not null references projects(id)     on delete cascade,
  repository_id  uuid not null references repositories(id) on delete cascade,
  from_node_id   uuid not null references code_nodes(id)   on delete cascade,
  to_node_id     uuid references code_nodes(id)            on delete cascade,
  to_symbol      text,                              -- unresolved external target
  kind           code_edge_kind not null,
  resolved       boolean not null default true,
  created_at     timestamptz not null default now()
);
create index on code_edges (from_node_id, kind);
create index on code_edges (to_node_id,   kind);
```

> **Why Postgres tables rather than a graph database.** Our traversals are 1–2 hops from a known node — a bounded index lookup, not a graph algorithm. Postgres does this in single-digit milliseconds. Neo4j would earn its place only when we need unbounded traversal or path-finding, which nothing in the current design requires. Recorded as `ADR-008`.

---

## 7. Errors

```sql
create table raw_events (
  id            uuid not null default uuid_generate_v7(),
  project_id    uuid not null references projects(id) on delete cascade,
  api_key_id    uuid references api_keys(id),
  batch_id      uuid not null,

  received_at   timestamptz not null default now(),
  event_ts      timestamptz not null,
  environment   environment_kind not null,
  service       text,
  release       text,

  payload       jsonb not null,                    -- sanitised
  payload_url   text,                              -- full original in object storage
  payload_bytes integer not null,
  redactions    jsonb not null default '[]',       -- what was redacted, never what it was

  is_valid      boolean not null default true,
  validation_errors jsonb,
  processed_at  timestamptz,

  -- PostgreSQL requires every unique/primary-key constraint on a partitioned
  -- table to include all partitioning columns. See B3 in `18` §9.
  primary key (id, received_at)
) partition by range (received_at);

-- NO PARTITION IS CREATED HERE. rt_admin.ensure_partitions() creates and
-- secures every partition, in migration …001400 — see §12.10 (B13) and §15.
-- Inline DDL here would be a second creation path that runs exactly once and is
-- never exercised again, and that is precisely where B13 reopens.

create index on raw_events (project_id, received_at desc);
create index on raw_events (batch_id);
create index on raw_events using gin (payload jsonb_path_ops);
```

> `raw_events` is partitioned from day one. It is the highest-volume table by an order of magnitude, and retrofitting partitioning onto a 100M-row table is a painful migration nobody wants to do under load.

> **Composite primary key, and the invariant it creates.** `(id, received_at)` is not a style choice — PostgreSQL rejects `primary key (id)` on a table partitioned by `received_at`. The consequence is that **no other table may declare a foreign key to `raw_events(id)` alone**, because `id` by itself is not unique-constrained. `error_occurrences.raw_event_id` is therefore deliberately FK-free and is documented as a soft reference. Referential integrity for it is asserted by test, not by the database. The same applies to `error_occurrences(id)` and `investigations.trigger_occurrence_id`.

```sql
create table issues (
  id                  uuid primary key default uuid_generate_v7(),
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
  unique (project_id, fingerprint)
);

create index on issues (project_id, status, severity, last_seen desc);
create index on issues (project_id, last_seen desc);
create index on issues (project_id, occurrence_count desc);
create index issues_message_trgm on issues using gin (normalized_message gin_trgm_ops);

create table error_occurrences (
  id            uuid not null default uuid_generate_v7(),
  project_id    uuid not null references projects(id) on delete cascade,
  issue_id      uuid not null references issues(id)   on delete cascade,
  raw_event_id  uuid not null,                        -- soft reference; see the note above

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

  -- Partition key must be part of the primary key. See B3 in `18` §9.
  primary key (id, occurred_at)
) partition by range (occurred_at);

-- Again, no inline partition. Note that `occurred_at` is the CUSTOMER's
-- timestamp and S1 accepts events up to 7 days old, so partitions must exist
-- BEHIND the current month as well as ahead — see ensure_partitions(§12.10).

create index on error_occurrences (issue_id, occurred_at desc);
create index on error_occurrences (project_id, occurred_at desc);
create index on error_occurrences (raw_event_id);      -- soft-reference lookups
```

---

## 8. Investigations

```sql
create table investigations (
  id                uuid primary key default uuid_generate_v7(),
  project_id        uuid not null references projects(id) on delete cascade,
  issue_id          uuid not null references issues(id)   on delete cascade,
  repository_id     uuid references repositories(id),
  trigger_occurrence_id uuid,

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

create index on investigations (project_id, created_at desc);
create index on investigations (project_id, status, created_at desc);
create index on investigations (issue_id, created_at desc);
create index investigations_active_idx on investigations (project_id, status)
  where status in ('queued','analyzing','patching','validating','repairing',
                   'reviewing','scoring','publishing');

-- B8: the S3 investigation gate is a read, and two occurrences of the same
-- fingerprint arriving concurrently would both pass it. This constraint is what
-- actually enforces "never run two pipelines for the same bug". S3 catches the
-- unique_violation and attaches the occurrence instead of creating a second run.
create unique index investigations_one_active_per_issue
  on investigations (issue_id)
  where status in ('queued','analyzing','patching','validating','repairing',
                   'reviewing','scoring','publishing');
```

> The partial unique index above is a **cost control as much as a correctness control.** Without it, an error storm that delivers two occurrences of one fingerprint in the same instant pays for two complete pipelines (~$0.64) and opens two pull requests for one bug.

### Pipeline steps — the durability and audit backbone

```sql
create table pipeline_steps (
  id                uuid primary key default uuid_generate_v7(),
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
  unique (investigation_id, stage, attempt)
);
create index on pipeline_steps (investigation_id, sequence);
```

> This table is what makes the pipeline resumable (R3), idempotent (R2), and inspectable. The dashboard's pipeline viewer is a direct render of these rows. The `unique (investigation_id, stage, attempt)` constraint is the idempotency guard — a redelivered job cannot double-insert.

### LLM call ledger

```sql
create table llm_calls (
  id                uuid primary key default uuid_generate_v7(),
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
create index on llm_calls (project_id, created_at desc);
create index on llm_calls (investigation_id);
create index on llm_calls (project_id, stage, model, created_at desc);
create index on llm_calls (prompt_hash);
```

---

## 9. Pipeline artefacts

```sql
create table context_bundles (
  id                uuid primary key default uuid_generate_v7(),
  investigation_id  uuid not null unique references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,

  repository_id     uuid references repositories(id),
  commit_sha        text,
  ref               text,

  token_count       integer not null,
  token_budget      integer not null,
  file_count        integer not null,

  files             jsonb not null,     -- [{repo_path, strategy, relevance, line_range, ...}]
  content_url       text not null,      -- actual source in object storage (can be large)
  graph             jsonb not null,     -- {nodes:[], edges:[]}
  history           jsonb not null,     -- blame, recent commits, release diff, PRs
  tests             jsonb not null,
  strategy_stats    jsonb not null,
  quality_score     numeric(4,3) not null,
  quality_signals   jsonb not null,
  gaps              text[] not null default '{}',

  created_at        timestamptz not null default now()
);

create table root_cause_analyses (
  id                uuid primary key default uuid_generate_v7(),
  investigation_id  uuid not null unique references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,

  summary           text not null,
  mechanism         text not null,
  category          text not null,
  introduced_by_sha text,
  introduced_by_author text,
  introduced_by_date timestamptz,

  blast_radius      jsonb not null,
  reasoning_chain   jsonb not null,     -- the full step array, evidence included
  eliminated_hypotheses jsonb not null default '[]',
  fix_strategy      jsonb not null,

  self_assessed_confidence numeric(4,3),
  uncertainty_notes text[],
  evidence_validation jsonb not null,   -- which findings passed/failed binding checks

  model             text not null,
  prompt_version    text not null,
  created_at        timestamptz not null default now()
);

create table patches (
  id                uuid primary key default uuid_generate_v7(),
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

create table validation_runs (
  id                uuid primary key default uuid_generate_v7(),
  investigation_id  uuid not null references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,
  patch_id          uuid not null references patches(id) on delete cascade,

  attempt           integer not null default 1,
  passed            boolean not null,
  mode              validation_mode not null default 'full',
  failed_gate       text,

  gates             jsonb not null,                -- per-gate results
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

create table critiques (
  id                uuid primary key default uuid_generate_v7(),
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

  model             text not null,
  prompt_version    text not null,
  created_at        timestamptz not null default now()
);

create table confidence_scores (
  id                uuid primary key default uuid_generate_v7(),
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
```

---

## 10. Delivery and feedback

```sql
create table pull_request_records (
  id                uuid primary key default uuid_generate_v7(),
  investigation_id  uuid not null unique references investigations(id) on delete cascade,
  project_id        uuid not null references projects(id) on delete cascade,
  repository_id     uuid references repositories(id),

  is_simulated      boolean not null default false,   -- V1 fixture mode
  github_pr_number  integer,
  github_pr_id      bigint,
  url               text,
  branch_name       text not null,
  commit_sha        text,
  base_sha          text not null,

  title             text not null,
  body              text not null,
  is_draft          boolean not null default false,
  labels            text[] not null default '{}',

  state             text not null default 'open',     -- open|merged|closed
  merged_at         timestamptz,
  merged_by         text,
  closed_at         timestamptz,
  human_commits_added integer not null default 0,

  ci_status         text,                              -- V2
  ci_checked_at     timestamptz,

  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);

create table feedback_events (
  id                uuid primary key default uuid_generate_v7(),
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
create index on feedback_events (project_id, outcome, created_at desc);

-- V4 chat, schema present from V1 so no migration is needed later
create table investigation_messages (
  id                uuid primary key default uuid_generate_v7(),
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
create index on investigation_messages (investigation_id, created_at);
```

---

## 11. Platform tables

```sql
create table audit_log (
  id            uuid primary key default uuid_generate_v7(),
  project_id    uuid references projects(id) on delete cascade,
  organization_id uuid references organizations(id) on delete cascade,
  actor_user_id uuid references auth.users(id),
  actor_type    text not null default 'user',     -- user | system | github_app
  action        text not null,                    -- "api_key.created", "github.pr.created"
  target_type   text,
  target_id     text,
  metadata      jsonb not null default '{}',
  ip_address    inet,
  user_agent    text,
  created_at    timestamptz not null default now()
);
create index on audit_log (project_id, created_at desc);
create index on audit_log (actor_user_id, created_at desc);
create index on audit_log (action, created_at desc);

-- append-only enforcement
revoke update, delete on audit_log from authenticated, service_role;

create table usage_daily (
  project_id      uuid not null references projects(id) on delete cascade,
  day             date not null,
  events_ingested bigint not null default 0,
  issues_created  integer not null default 0,
  investigations_started integer not null default 0,
  investigations_completed integer not null default 0,
  prs_opened      integer not null default 0,
  prs_merged      integer not null default 0,
  tokens_in       bigint not null default 0,
  tokens_out      bigint not null default 0,
  llm_cost_micro_usd bigint not null default 0,
  sandbox_seconds integer not null default 0,
  primary key (project_id, day)
);
```

---

## 12. Row-level security

This is the load-bearing security control. **26 tables** carry RLS: 22 project-scoped, 3 organization-scoped, and 1 dual-scope (`audit_log`). Nothing that holds tenant data is exempt.

Of those 26, **20 share one generic policy shape** (§12.8) and **6 need bespoke policies** (§12.4, §12.6, §12.7) because they are keyed on something other than `project_id`.

### 12.1 How `FORCE ROW LEVEL SECURITY` actually behaves

This must be stated correctly, because getting it wrong is what produced blockers B1, B2, and B4.

| Claim | Truth |
|---|---|
| `ENABLE` alone protects the table | No. The table **owner** is exempt until you also `FORCE` |
| `FORCE` exempts `SECURITY DEFINER` functions | **No.** `FORCE` exempts nothing. A `SECURITY DEFINER` function is still subject to RLS |
| Something must be able to bypass RLS | Yes — and the *only* thing that does is a role holding **`BYPASSRLS`** |

So a `SECURITY DEFINER` helper bypasses RLS **if and only if its owner holds `BYPASSRLS`.** That single fact is why the original design deadlocked: `auth_project_ids()` read `projects`, whose policy called `auth_project_ids()`, and `SECURITY DEFINER` did not break the cycle. The model below does not rely on that mechanism at all — it removes the cycle instead of privileging its way out (ADR-009).

### 12.2 The `rt_auth` helper schema

**No privileged role.** The six helpers are plain `stable` SQL functions — *not* `SECURITY DEFINER` — executing as the caller under the caller's own policies. Nothing in this system holds `BYPASSRLS` except Supabase's own `service_role`, which the workers use (ADR-009, Option B).

That works because every helper asks a question about the **caller** — which projects am I in, may I write here, am I an owner — and all of them are answerable from the caller's own rows. The membership policies are therefore `user_id = rt_auth.uid()`, with no subquery to recurse into.

```sql
create schema if not exists rt_auth;
revoke all on schema rt_auth from public;
grant usage on schema rt_auth to authenticated;
```

**`rt_auth.uid()`, not `auth.uid()`.** Discovered while the helpers were still `SECURITY DEFINER`: a definer function cannot reach the `auth` schema unless its owner has `USAGE`, and `grant usage on schema auth` run as `postgres` emits `WARNING: no privileges were granted` — the schema belongs to `supabase_admin`. A warning does not fail a migration, so the fix appears to apply and does nothing. Retained after the helpers became plain functions, because it removes a cross-schema dependency for a value that is one line of `current_setting`:

```sql
create or replace function rt_auth.uid() returns uuid
language sql stable
set search_path = pg_catalog
as $$
  select nullif(current_setting('request.jwt.claims', true)::jsonb ->> 'sub', '')::uuid
$$;
```

Every helper and every policy below uses this one definition.

> Each of these three was found by a migration failing loudly on the first `supabase db reset`, which is the intended behaviour and the reason the assertions run at all. None of them was visible from the specification.

```sql
-- Organizations the caller belongs to. Reads only the caller's own rows.
create or replace function rt_auth.org_ids() returns setof uuid
language sql stable
set search_path = pg_catalog, public          -- pinned: defeats search_path injection
as $$
  select om.organization_id
    from public.organization_members om
   where om.user_id = rt_auth.uid()
$$;

-- Projects the caller can see: direct membership, or membership of the owning
-- org. The read of `projects` is itself filtered by projects' own policy, which
-- reads only membership rows — one hop, no cycle.
create or replace function rt_auth.project_ids() returns setof uuid
language sql stable
set search_path = pg_catalog, public
as $$
  select pm.project_id
    from public.project_members pm
   where pm.user_id = rt_auth.uid()
  union
  select p.id
    from public.projects p
   where p.deleted_at is null
     and p.organization_id in (select om.organization_id
                                 from public.organization_members om
                                where om.user_id = rt_auth.uid())
$$;

-- Write authority on a project: project owner/maintainer, or org owner/maintainer.
create or replace function rt_auth.can_write_project(pid uuid) returns boolean
language sql stable
set search_path = pg_catalog, public
as $$
  select exists (select 1 from public.project_members pm
                  where pm.project_id = pid and pm.user_id = rt_auth.uid()
                    and pm.role in ('owner','maintainer'))
      or exists (select 1 from public.projects p
                   join public.organization_members om
                     on om.organization_id = p.organization_id
                  where p.id = pid and om.user_id = rt_auth.uid()
                    and om.role in ('owner','maintainer'))
$$;

-- Administrative authority: may alter MEMBERSHIP. Strictly narrower than write,
-- and that gap is what makes "maintainer promotes self to owner" an absent
-- capability rather than a policy nuance (B4).
create or replace function rt_auth.is_project_admin(pid uuid) returns boolean
language sql stable
set search_path = pg_catalog, public
as $$
  select exists (select 1 from public.project_members pm
                  where pm.project_id = pid and pm.user_id = rt_auth.uid()
                    and pm.role = 'owner')
      or exists (select 1 from public.projects p
                   join public.organization_members om
                     on om.organization_id = p.organization_id
                  where p.id = pid and om.user_id = rt_auth.uid()
                    and om.role = 'owner')
$$;

create or replace function rt_auth.is_org_owner(oid uuid) returns boolean
language sql stable
set search_path = pg_catalog, public
as $$
  select exists (select 1 from public.organization_members om
                  where om.organization_id = oid and om.user_id = rt_auth.uid()
                    and om.role = 'owner')
$$;
```

None of the six is `SECURITY DEFINER`. Each runs with exactly the privilege the caller already holds — the property the previous design could not offer, per ADR-009 Option B.

Every helper is then locked down:

```sql
revoke execute on all functions in schema rt_auth from public;
grant  execute on all functions in schema rt_auth to authenticated;
```

**Why this is not an escalation path.** Four properties, each independently checked by a test:

1. **No helper accepts a user identifier.** Every one derives identity from `rt_auth.uid()` internally. There is no way to ask "what can *someone else* see" — the API surface simply does not express it.
2. **`search_path` is pinned** on every function, so a caller cannot shadow `public.project_members` with a temp table and feed a helper attacker-controlled rows.
3. **`EXECUTE` is revoked from `PUBLIC`** and granted only to `authenticated`. `anon` cannot call them at all.
4. **None of them is `SECURITY DEFINER`.** No helper runs with authority the caller does not already hold, which is the strongest form of this constraint rather than an approximation of it. The only `SECURITY DEFINER` object left is the last-owner trigger (§12.5), which answers a cardinality question no single row can and returns a boolean, never rows.

### 12.3 Non-recursion argument (B2)

Termination comes from the shape of the graph, not from a role that opts out of it:

```
policy on project_members      → user_id = rt_auth.uid()          → terminates immediately
policy on organization_members → user_id = rt_auth.uid()          → terminates immediately
policy on projects  (SELECT)   → INLINE reads of the two above    → terminates in one hop
policy on projects  (write)    → can_write_project() → projects → INLINE policy → terminates
policy on organizations        → rt_auth.org_ids() → org_members → terminates
every other tenant table       → rt_auth.project_ids() → members + projects → terminates
```

Two rules keep `projects` terminating, and **both were found by a `db reset` failing, not by reading the graph**:

| Rule | What breaks without it |
|---|---|
| `projects`' SELECT policy is **inline**, never `project_ids()` | That helper reads `projects`, so the policy re-enters itself: `stack depth limit exceeded` |
| `projects`' write policies are **per-command**, never `for all` | A `for all` policy's `USING` is evaluated on `SELECT` too, so every read of `projects` calls a function that reads `projects` |

`projects` is the only table under this constraint, because it is the only one a helper reads. The 20 generic tables may call the helpers freely.

**A policy may never contain a self-referential subquery**, and no helper may read a table whose policy calls that helper. Both are checked in review and by the architecture regression tests in `14` §4.1a.

### 12.4 Identity tables — bespoke policies

```sql
-- organizations: organization-scoped, keyed on id
alter table organizations enable row level security;
alter table organizations force  row level security;

create policy organizations_read on organizations for select
  using (id in (select rt_auth.org_ids()));

create policy organizations_write on organizations for all
  using      (rt_auth.is_org_owner(id))
  with check (rt_auth.is_org_owner(id));

-- organization_members: read own row, or co-members of your orgs
alter table organization_members enable row level security;
alter table organization_members force  row level security;

-- OWN ROW ONLY. The co-member clause is deliberately absent: reading other
-- members' rows from this table's own policy is what forced a BYPASSRLS role in
-- the original design, and written inline it raises `infinite recursion
-- detected in policy`. V1 has no way to add a second member (invites are V2),
-- so one row IS the roster. See ADR-009.
create policy org_members_read on organization_members for select
  using (user_id = rt_auth.uid());

create policy org_members_insert on organization_members for insert
  with check (rt_auth.is_org_owner(organization_id));

create policy org_members_update on organization_members for update
  using      (rt_auth.is_org_owner(organization_id))
  with check (rt_auth.is_org_owner(organization_id));

create policy org_members_delete on organization_members for delete
  using (rt_auth.is_org_owner(organization_id));

-- github_installations: organization-scoped (no project_id column).
-- Worker-only in V1; the policy exists now so the V2 dashboard read is already safe.
alter table github_installations enable row level security;
alter table github_installations force  row level security;

create policy gh_installations_read on github_installations for select
  using (organization_id in (select rt_auth.org_ids()));

create policy gh_installations_write on github_installations for all
  using      (rt_auth.is_org_owner(organization_id))
  with check (rt_auth.is_org_owner(organization_id));

-- project_members: read own row, or co-members of your projects
alter table project_members enable row level security;
alter table project_members force  row level security;

-- Own row only, for the same reason as organization_members above.
create policy project_members_read on project_members for select
  using (user_id = rt_auth.uid());

create policy project_members_insert on project_members for insert
  with check (rt_auth.is_project_admin(project_id));

create policy project_members_update on project_members for update
  using      (rt_auth.is_project_admin(project_id))
  with check (rt_auth.is_project_admin(project_id));

create policy project_members_delete on project_members for delete
  using (rt_auth.is_project_admin(project_id));
```

**Why this forecloses privilege escalation (B4).** Membership writes are gated on `is_project_admin` / `is_org_owner` — **owner only**, deliberately narrower than `can_write_project`. A maintainer therefore has no write path to a membership table at all, so "maintainer promotes self to owner" is not a policy nuance, it is an absent capability. A non-member has no path either: `is_project_admin` returns false, so both `USING` and `WITH CHECK` fail, and "add myself to someone else's project" is rejected by the `INSERT` policy.

`WITH CHECK` is applied on `UPDATE` as well as `USING`, so an admin cannot move a row *into* a project they don't administer.

### 12.5 The last-owner invariant

RLS cannot express "an organization must retain at least one owner" — that is a cardinality rule, not a row predicate. It needs a trigger:

```sql
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

create trigger org_members_keep_owner  before delete or update on organization_members
  for each row execute function rt_auth.assert_owner_remains();
create trigger project_members_keep_owner before delete or update on project_members
  for each row execute function rt_auth.assert_owner_remains();
```

### 12.6 `projects` — bespoke, keyed on `id` (B1)

`projects` has no `project_id` column; its identity **is** `id`. It must never be run through the generic loop.

```sql
alter table projects enable row level security;
alter table projects force  row level security;

-- INLINE — never project_ids(). That helper reads projects, so a policy
-- calling it re-enters itself: stack depth limit exceeded.
create policy projects_read on projects for select
  using (
       id in (select pm.project_id from project_members pm
               where pm.user_id = rt_auth.uid())
    or organization_id in (select om.organization_id from organization_members om
                            where om.user_id = rt_auth.uid())
  );

-- PER-COMMAND — never for all. A for all policy's USING is evaluated on
-- SELECT too, which would make every read of projects call a function
-- (can_write_project) that reads projects.
create policy projects_insert on projects for insert
  with check (rt_auth.can_write_project(id));

create policy projects_update on projects for update
  using      (rt_auth.can_write_project(id))
  with check (rt_auth.can_write_project(id));

create policy projects_delete on projects for delete
  using (rt_auth.can_write_project(id));
```

### 12.7 `audit_log` — dual-scope (B5)

`audit_log.project_id` is nullable by design: `installation.created` and other `actor_type = 'github_app'` events are organization-scoped. Under a `project_id in (...)` policy, `NULL IN (…)` evaluates to `NULL`, never `true`, so **those rows would be invisible to everyone forever** — including during the `audit_log` blast-radius query that `11` §10's SEV1 runbook depends on.

```sql
-- No row may be unattributable to a tenant.
alter table audit_log add constraint audit_log_scope_ck
  check (project_id is not null or organization_id is not null);

alter table audit_log enable row level security;
alter table audit_log force  row level security;

create policy audit_read on audit_log for select
  using (
       (project_id is not null and project_id in (select rt_auth.project_ids()))
    or (project_id is null and organization_id is not null
        and rt_auth.is_org_owner(organization_id))
  );

-- Append-only, and only the worker appends. No UPDATE/DELETE policy exists at all.
revoke insert, update, delete on audit_log from authenticated;
revoke         update, delete on audit_log from service_role;
```

Organization-level events are visible to **organization owners only** — deliberately narrower than project-level events, because they concern the installation and billing surface rather than a single project's activity.

### 12.8 The generic loop — the 20 remaining project-scoped tables

Every table below carries a real `project_id` column, so one policy shape genuinely fits all of them.

```sql
do $$
declare t text;
begin
  foreach t in array array[
    'api_keys','repositories','code_nodes','code_edges',
    'raw_events','issues','error_occurrences','investigations','pipeline_steps',
    'llm_calls','context_bundles','root_cause_analyses','patches','validation_runs',
    'critiques','confidence_scores','pull_request_records','feedback_events',
    'investigation_messages','usage_daily'
  ] loop
    execute format('alter table %I enable row level security', t);
    execute format('alter table %I force  row level security', t);
    execute format($f$
      create policy tenant_read on %I for select
        using (project_id in (select rt_auth.project_ids()))
    $f$, t);
    execute format($f$
      create policy tenant_write on %I for all
        using      (project_id in (select rt_auth.project_ids())
                    and rt_auth.can_write_project(project_id))
        with check (project_id in (select rt_auth.project_ids())
                    and rt_auth.can_write_project(project_id))
    $f$, t);
  end loop;
end $$;
```

`projects`, `organizations`, `organization_members`, `github_installations`, `project_members`, and `audit_log` are **excluded from this loop by design** — each has a bespoke policy above, because none of them is keyed on a plain `project_id`. A test asserts that the loop array (20) and the bespoke set (6) together cover exactly the 26 RLS-protected tables, so a newly added table cannot be silently forgotten.

### 12.9 Coverage assertions

Three assertions, because "RLS enabled", "RLS effective", and "RLS everywhere we said" are different claims.

**There is no exemption list.** One previously excluded `schema_migrations`, a table that never appears in `public` — the Supabase CLI keeps its history in the `supabase_migrations` schema. A standing exemption for a hypothetical table is fail-open: it silently covers whatever later happens to match the name. If a legitimate non-tenant table ever lands in `public`, the assertion fires and we decide deliberately.

> **What these assertions cannot do.** They check *structure* — enabled, forced, policy present. They cannot tell you a policy is **evaluable**: all three passed while every policy raised `permission denied for schema auth` at query time (§12.2). That gap is covered by the positive-control read in `14` §4.1, and it is the reason a purely negative isolation suite is insufficient — "user A sees zero of B's rows" is equally true when nothing works at all.

```sql
-- (1) Fails the migration if any relation holding tenant data lacks forced RLS.
--     relkind 'p' = partitioned parent, 'r' = ordinary table AND every partition.
--     Partitions are deliberately IN SCOPE — see §12.10.
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

-- (2) Fails the migration if RLS is enabled but NO policy exists.
--     Enabled-with-no-policy is default-deny: it satisfies assertion (1) while
--     silently returning zero rows to every caller, including legitimate ones.
--     That failure mode is invisible until a query mysteriously returns nothing.
do $$
declare missing text;
begin
  select string_agg(c.relname, ', ') into missing
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relkind in ('r','p')
     and c.relrowsecurity
     and not exists (select 1 from pg_policy p where p.polrelid = c.oid);
  if missing is not null then
    raise exception 'RLS enabled but no policy on: %', missing;
  end if;
end $$;

-- (3) Fails the migration if the logical table count drifts from the 26 fixed in
--     `18` §6. Partitions are excluded here (they inherit from a parent), so this
--     counts logical tables only: 20 generic + 6 bespoke. A new table added
--     without a policy, or an old one quietly dropped from the loop array, fails
--     the run rather than drifting away from the documented figure.
do $$
declare n int;
begin
  select count(*) into n
    from pg_class c join pg_namespace ns on ns.oid = c.relnamespace
   where ns.nspname = 'public' and c.relkind in ('r','p') and c.relrowsecurity
     and not exists (select 1 from pg_inherits i where i.inhrelid = c.oid);
  if n <> 26 then
    raise exception 'expected 26 RLS-protected logical tables, found %', n;
  end if;
end $$;
```

### 12.10 Partitions do not inherit RLS (B13)

**This is the most easily missed hole in the entire tenancy model**, because everything about it looks correct until someone names a partition directly.

| Access path | Whose policies apply |
|---|---|
| `select * from raw_events` (parent) | The **parent's** policies — correct |
| `select * from raw_events_2026_08` (partition, directly) | **Only that partition's own policies.** The parent's are not consulted |

A partition created without RLS is therefore a complete tenant-isolation bypass for anyone who can name it, and `authenticated` holds `SELECT` on it through Supabase's schema-wide grants. `alter table raw_events enable row level security` sets the flag on the parent only; partitions are independent relations with independent flags.

**We do not exclude partitions from the coverage assertion.** Excluding them would make the migration pass while leaving the hole open — the assertion firing is the system working correctly. Every partition is secured at creation instead:

```sql
create schema if not exists rt_admin;
revoke all on schema rt_admin from public, anon, authenticated;

-- Applies the standard project-scoped policy set to one partition.
-- Runs as the schema owner during migration and from the maintenance job.
-- NOT security definer: it must not be callable by anyone who isn't already
-- entitled to create policies.
create or replace function rt_admin.secure_partition(p_table text) returns void
language plpgsql
set search_path = pg_catalog, public
as $$
begin
  execute format('alter table public.%I enable row level security', p_table);
  execute format('alter table public.%I force  row level security', p_table);

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
end $$;

revoke execute on function rt_admin.secure_partition(text) from public, anon, authenticated;
```

The policies are **identical to the parent's**, so the two access paths cannot diverge in behaviour — only in which relation enforces them.

**The maintenance job carries the same obligation.** It pre-creates three months of partitions ahead (`04` §15), and a partition created without this call reopens the hole silently, on a monthly cadence, with no code change to review:

```sql
create or replace function rt_admin.ensure_partitions(
  p_months_ahead int default 3,
  p_months_behind int default 1        -- see the note below; NOT optional in practice
) returns void language plpgsql
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
```

**`p_months_behind` is not a convenience.** `error_occurrences` is partitioned on `occurred_at` — the *customer's* timestamp, not ours — and S1 accepts events up to 7 days old (`RT-INGEST-0012`). On the 1st to 7th of any month a perfectly valid event carries last month's date. Current-month-forward only makes that insert fail with `no partition of relation found for row`: intermittent, confined to the first week of a month, and therefore the worst possible failure signature. A `DEFAULT` partition would also prevent the error and is deliberately **not** used — rows land in it silently and detaching one later is painful, which trades a loud failure for a quiet one.

`secure_partition()` also issues the partition's `GRANT`. Privileges are checked against the relation actually named in a query, and grants no more propagate from parent to partition than policies do; without it a direct partition read fails with `permission denied`, which resembles isolation without being it — and would make the B13 regression test pass for the wrong reason.

Creation and securing live in **one function**, so there is no code path that produces an unsecured partition. A test asserts that every partition of every partitioned table has RLS forced and at least one policy — it runs after the maintenance job in CI, not only after the initial migration, because the monthly job is where this would regress.

### Worker access

Workers connect as `service_role`, which bypasses RLS by design — a worker legitimately processes many tenants. The compensating controls:

1. Every worker query passes `project_id` explicitly. This is enforced by a repository-layer base class that refuses to build a query without it.
2. A test suite asserts that for every tenant table, a query without `project_id` raises.
3. `service_role` credentials exist only in the worker environment, never in the API service, and never anywhere near a sandbox.

---

## 13. Materialised views for dashboard performance

```sql
create materialized view issue_hourly_counts as
select issue_id, project_id,
       date_trunc('hour', occurred_at) as hour,
       count(*) as occurrences,
       count(distinct user_hash) as affected_users
from error_occurrences
where occurred_at > now() - interval '30 days'
group by 1,2,3;

create unique index on issue_hourly_counts (issue_id, hour);
-- refreshed every 5 minutes, concurrently
```

> **Materialised views cannot carry RLS (B6).** PostgreSQL does not support row-level security on a materialised view, and a matview does *not* inherit the policies of the tables it was built from. Both views below contain per-project rows, so direct access by `authenticated` would be an unrestricted cross-tenant aggregate leak — a user could read another tenant's error volumes and health scores. This is enforced, not assumed:

```sql
revoke all on issue_hourly_counts  from anon, authenticated;
revoke all on project_health_daily from anon, authenticated;
```

```sql
create materialized view project_health_daily as
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
```

### 13.1 The controlled access path

Since the views are unreadable by `authenticated`, every read goes through a `SECURITY DEFINER` accessor that re-applies tenant scoping. The filter is inside the function, so the caller cannot remove it.

```sql
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

-- SECURITY DEFINER here bypasses no policy: a matview carries no RLS at all
-- (that is B6). These need only the SELECT privilege `authenticated` was
-- revoked, so they keep the migration role as owner.
revoke execute on function public.issue_hourly_counts_for(uuid, timestamptz, timestamptz) from public;
revoke execute on function public.project_health_daily_for(uuid, date, date) from public;
grant  execute on function public.issue_hourly_counts_for(uuid, timestamptz, timestamptz) to authenticated;
grant  execute on function public.project_health_daily_for(uuid, date, date) to authenticated;
```

Note the belt-and-braces on `project_health_daily_for`: it takes an explicit `p_project_id` **and** intersects with `rt_auth.project_ids()`. Passing another tenant's project id returns zero rows rather than an error, so the function is not an existence oracle either.

---

## 14. Retention

| Table | Retention | Mechanism |
|---|---|---|
| `raw_events` | 30 d hot, 90 d cold, then delete | Drop monthly partitions; archive to object storage first |
| `error_occurrences` | 90 d detail; aggregates kept indefinitely | Drop partitions after rolling into `issue_hourly_counts` |
| `issues` | Indefinite | Small table, high value |
| `investigations` + artefacts | Indefinite (plan-limited) | Free plan: 90 d. Paid: indefinite |
| `pipeline_steps` | Follows the investigation | Cascade delete |
| `llm_calls` | 1 year | Needed for cost analysis and calibration |
| Object storage (prompts, transcripts, diffs) | 180 d, then lifecycle to cold | S3-style lifecycle rules |
| `audit_log` | 2 years minimum | Compliance requirement |
| `usage_daily` | Indefinite | Tiny, billing-relevant |

### 14.1 Retention bounds replay (C9)

`02` §4 and `05` §6.3 promise that any investigation is replayable. That promise is **bounded by `raw_events` retention**, and the two must not be stated independently:

- An investigation is retained indefinitely (plan-limited), but replay reconstructs from the **source event**, which is deleted at 90 days.
- Therefore `POST /v1/investigations/{id}/replay` is available only while the triggering `raw_event` still exists.
- The API surfaces `replay_available_until` on the investigation resource, computed as `trigger_event.received_at + retention_window`. After it passes, replay returns `RT-NOTFOUND-0002` (source event expired) rather than failing obscurely.
- Free-plan projects retain investigations 90 days, so replay and investigation retention expire together. On paid plans the investigation outlives its replayability, which is why the field is explicit rather than inferred.

---

## 15. Migration discipline

```
infra/supabase/migrations/
├─ 20260801000000_extensions_and_enums.sql
├─ 20260801000100_identity_and_tenancy.sql
├─ 20260801000200_github.sql
├─ 20260801000300_code_index.sql
├─ 20260801000400_errors.sql
├─ 20260801000500_investigations.sql
├─ 20260801000600_artifacts.sql
├─ 20260801000700_delivery_feedback.sql
├─ 20260801000800_platform.sql
├─ 20260801000900_auth_helpers.sql          ← rt_auth schema + 6 plain helpers (§12.2)
├─ 20260801001000_rls_policies.sql          ← bespoke + generic policies (§12.4–12.8)
├─ 20260801001100_membership_triggers.sql   ← last-owner invariant (§12.5)
├─ 20260801001200_partition_security.sql    ← rt_admin.secure_partition + ensure_partitions (§12.10)
├─ 20260801001300_materialized_views.sql    ← views + REVOKE + accessors (§13)
├─ 20260801001400_partition_maintenance.sql ← CREATES the first partitions, then schedules ensure_partitions()
└─ 20260801001500_rls_assertions.sql        ← both coverage assertions (§12.9), LAST
```

**15 migrations. Ordering is load-bearing:**

| Constraint | Why |
|---|---|
| `auth_helpers` before `rls_policies` | Every policy references `rt_auth.*` |
| `partition_security` before `partition_maintenance` | `ensure_partitions()` is defined there and called there |
| **No partition is created before `partition_maintenance`** | A partition's policies reference `rt_auth.*` (`…000900`) and are applied by `secure_partition()` (`…001200`). Creating one in `…000400`, as this document originally did, would make migration 5 depend on migrations 10 and 13 — and would be a second creation path, never exercised again, which is exactly where B13 reopens |
| `materialized_views` after `auth_helpers` | Accessors call `rt_auth.project_ids()` |
| **`rls_assertions` LAST** | It is the gate. Running it earlier would fire on tables that are legitimately not yet secured |

Placing the assertions in their own final migration is deliberate: they assert the *finished* state, so any migration that adds a relation without securing it fails the run — including migrations written months from now by someone who has never read this file.

Rules:

- Migrations are forward-only. A mistake is corrected by a new migration, never by editing an applied one.
- Every migration is idempotent (`if not exists` / `or replace`) so re-runs are safe.
- Destructive changes are two-phase: deploy code tolerating both shapes, then migrate, then remove the tolerance.
- Index creation on large tables uses `create index concurrently`.
- Every migration is tested against a seeded database in CI before merge.

---

## 16. Table register

Every table, classified by scope with an unambiguous authorization path. **Scope** determines which policy shape applies; **auth path** is the exact predicate a reader must satisfy.

Legend — `ORG` organization-scoped · `PRJ` project-scoped · `USR` user-scoped · `SYS` system-scoped (no tenant data).

### Organization-scoped

| Table | PK | FKs | Auth path | Retention | Sensitive | Audited |
|---|---|---|---|---|---|---|
| `organizations` | `id` | — | `id ∈ rt_auth.org_ids()`; write `is_org_owner(id)` | Indefinite | — | create, delete, plan change |
| `organization_members` | `(organization_id, user_id)` | → `organizations`, `auth.users` | own row, or `organization_id ∈ rt_auth.org_ids()`; write `is_org_owner` | Indefinite | role | **all writes** |
| `github_installations` | `id` | → `organizations` | `organization_id ∈ rt_auth.org_ids()`; write `is_org_owner` | Until uninstalled | `permissions` | install, delete, suspend |

### Project-scoped

| Table | PK | FKs | Auth path | Retention | Sensitive | Audited |
|---|---|---|---|---|---|---|
| `projects` | `id` | → `organizations` | `id ∈ rt_auth.project_ids()`; write `can_write_project(id)` | Indefinite | `settings`, cost caps | create, delete, settings change |
| `project_members` | `(project_id, user_id)` | → `projects`, `auth.users` | own row, or `project_id ∈ rt_auth.project_ids()`; write `is_project_admin` | Indefinite | role | **all writes** |
| `api_keys` | `id` | → `projects`, `auth.users` | generic | Until revoked | `key_hash` | create, revoke, rotate |
| `repositories` | `id` | → `projects`, `github_installations` | generic | Until disconnected | `path_mappings` | connect, disconnect |
| `code_nodes` | `id` | → `projects`, `repositories` | generic | Until re-index | `source` | — |
| `code_edges` | `id` | → `projects`, `repositories`, `code_nodes` | generic | Until re-index | — | — |
| `raw_events` | `(id, received_at)` | → `projects`, `api_keys` | generic | 30 d hot / 90 d cold | `payload` (post-redaction) | raw-blob access only |
| `issues` | `id` | → `projects`, `issues` (self) | generic | Indefinite | — | resolve, mute |
| `error_occurrences` | `(id, occurred_at)` | → `projects`, `issues` | generic | 90 d | `stack_frames`, `breadcrumbs` | — |
| `investigations` | `id` | → `projects`, `issues`, `repositories`, self | generic | Plan-limited | — | cancel, replay |
| `pipeline_steps` | `id` | → `investigations`, `projects` | generic | Follows investigation | `output_summary` | — |
| `llm_calls` | `id` | → `investigations`, `projects`, `pipeline_steps` | generic | 1 year | prompt/response URLs | — |
| `context_bundles` | `id` | → `investigations`, `projects`, `repositories` | generic | Follows investigation | **customer source** | — |
| `root_cause_analyses` | `id` | → `investigations`, `projects` | generic | Follows investigation | source excerpts | — |
| `patches` | `id` | → `investigations`, `projects` | generic | Follows investigation | **diff content** | — |
| `validation_runs` | `id` | → `investigations`, `projects`, `patches` | generic | Follows investigation | transcript URL | — |
| `critiques` | `id` | → `investigations`, `projects`, `patches` | generic | Follows investigation | — | — |
| `confidence_scores` | `id` | → `investigations`, `projects` | generic | Follows investigation | — | — |
| `pull_request_records` | `id` | → `investigations`, `projects`, `repositories` | generic | Follows investigation | PR body | **PR creation** |
| `feedback_events` | `id` | → `investigations`, `projects` | generic | Follows investigation | edit diff URL | — |
| `investigation_messages` | `id` | → `investigations`, `projects`, `auth.users` | generic | Follows investigation | message content | — |
| `usage_daily` | `(project_id, day)` | → `projects` | generic | Indefinite | — | — |

### Dual-scope

| Table | PK | Auth path | Retention | Audited |
|---|---|---|---|---|
| `audit_log` | `id` | `project_id ∈ rt_auth.project_ids()` **OR** (`project_id IS NULL` ∧ `is_org_owner(organization_id)`) | 2 years min | is the audit |

### User-scoped / system-scoped

| Table | Scope | Notes |
|---|---|---|
| `auth.users` | USR | Supabase GoTrue. Not ours to police; we never join PII out of it |
| `schema_migrations` | SYS | No tenant data. Exempt from the §12.9 coverage assertion |

**Totals: 26 RLS-protected tables** — 3 organization-scoped, 22 project-scoped (`projects` keyed on `id`, `project_members` on a composite, 20 on a plain `project_id`), 1 dual-scope. Split by policy shape: **20 generic + 6 bespoke.** `github_installations` is worker-only in V1 but carries RLS from day one so the V2 dashboard read is already safe.

---

*Next: [`05-API-SPEC.md`](./05-API-SPEC.md)*
