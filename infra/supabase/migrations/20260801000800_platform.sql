-- 000800 — platform tables.
--
-- docs/04-DATA-MODEL.md §11.

create table if not exists audit_log (
  id            uuid primary key default public.uuid_generate_v7(),
  -- NULLABLE BY DESIGN, and the reason is B5: `installation.created` and other
  -- actor_type='github_app' events are organization-scoped, with no project.
  -- The dual-branch policy in 001000 is what keeps those rows visible to
  -- somebody — under a plain `project_id in (...)` policy, `NULL IN (...)`
  -- evaluates to NULL rather than true and they would be invisible forever,
  -- including during the SEV1 blast-radius query in docs/11 §10.
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
-- Project activity feed (Settings → Audit log).
create index if not exists audit_log_project_idx on audit_log (project_id, created_at desc);
-- "What did this user do" — the incident-response query.
create index if not exists audit_log_actor_idx on audit_log (actor_user_id, created_at desc);
-- "Who did X" — action-first search.
create index if not exists audit_log_action_idx on audit_log (action, created_at desc);

create table if not exists usage_daily (
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
