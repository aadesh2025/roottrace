-- 000100 — identity and tenancy.
--
-- docs/04-DATA-MODEL.md §4. These five tables are the root of the authorization
-- model: every RLS policy in 001000 resolves, directly or indirectly, to
-- membership rows in `organization_members` and `project_members`.

create table if not exists organizations (
  id            uuid primary key default public.uuid_generate_v7(),
  name          text not null,
  slug          text not null unique check (slug ~ '^[a-z0-9-]{2,48}$'),
  plan          project_plan not null default 'free',
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  deleted_at    timestamptz
);

create table if not exists organization_members (
  organization_id uuid not null references organizations(id) on delete cascade,
  user_id         uuid not null references auth.users(id)    on delete cascade,
  role            member_role not null default 'viewer',
  invited_by      uuid references auth.users(id),
  created_at      timestamptz not null default now(),
  primary key (organization_id, user_id)
);

create table if not exists projects (
  id                uuid primary key default public.uuid_generate_v7(),
  organization_id   uuid not null references organizations(id) on delete cascade,
  name              text not null,
  slug              text not null,
  description       text,

  -- Behavioural configuration; documented in docs/appendix/A3 §3.
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

  -- Cost control. Integers in micro-USD, never floats (CLAUDE.md conventions).
  daily_cost_cap_micro_usd    bigint not null default 5000000,   -- $5.00
  monthly_cost_cap_micro_usd  bigint not null default 100000000, -- $100.00
  cost_breaker_open           boolean not null default false,
  cost_breaker_opened_at      timestamptz,

  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  deleted_at        timestamptz,
  unique (organization_id, slug)
);

create table if not exists project_members (
  project_id  uuid not null references projects(id)     on delete cascade,
  user_id     uuid not null references auth.users(id)   on delete cascade,
  role        member_role not null default 'viewer',
  created_at  timestamptz not null default now(),
  primary key (project_id, user_id)
);
-- Serves rt_auth.project_ids(): `where user_id = auth.uid()`, evaluated on every
-- single RLS check in the system. The PK is (project_id, user_id), so it cannot
-- serve a user_id-leading lookup.
create index if not exists project_members_user_idx on project_members (user_id);

-- Same reasoning for the organization side.
create index if not exists organization_members_user_idx on organization_members (user_id);

create table if not exists api_keys (
  id            uuid primary key default public.uuid_generate_v7(),
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
-- No separate index on key_hash: the unique constraint already creates the btree
-- that serves `where key_hash = $1` on the ingest hot path. A second index would
-- never be chosen and would only add write cost (C8).
create index if not exists api_keys_project_active_idx
  on api_keys (project_id) where revoked_at is null;
