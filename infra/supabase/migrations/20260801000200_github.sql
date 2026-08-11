-- 000200 — GitHub installations and repositories.
--
-- docs/04-DATA-MODEL.md §5. Populated in V1 only by fixtures: GITHUB_MODE is
-- `fixture` throughout, and the `evaluation` deployment tier holds no App
-- private key, so no installation token can be minted (docs/A3 §1.1).

create table if not exists github_installations (
  id                    uuid primary key default public.uuid_generate_v7(),
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

create table if not exists repositories (
  id                uuid primary key default public.uuid_generate_v7(),
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
