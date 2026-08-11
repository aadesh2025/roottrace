-- 000300 — code index.
--
-- docs/04-DATA-MODEL.md §6. The schema exists from V1 and STAYS EMPTY: repo
-- indexing and embeddings are V2, and retrieval strategy C returns empty in V1
-- by design. The tables are created now so that enabling them later is a config
-- change rather than a migration against a live system.
--
-- Types and operator classes are schema-qualified because the extensions live in
-- `extensions`, not `public` — this must not depend on search_path.

create table if not exists code_nodes (
  id             uuid primary key default public.uuid_generate_v7(),
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

  embedding      extensions.vector(1536),
  embedding_model text,
  embedded_at    timestamptz,

  commit_sha     text not null,
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  unique (repository_id, repo_path, symbol_name, start_line)
);

-- Retrieval strategy A: resolve a stack frame path to its symbols.
create index if not exists code_nodes_repo_path_idx on code_nodes (repository_id, repo_path);
-- Retrieval strategy B: call-graph expansion from a named symbol.
create index if not exists code_nodes_symbol_idx    on code_nodes (repository_id, symbol_name);
-- Fuzzy symbol lookup when a frame names something close but not exact.
create index if not exists code_nodes_trgm_idx      on code_nodes
  using gin (symbol_name extensions.gin_trgm_ops);

-- HNSW over IVFFlat: better recall/latency and no retraining as the index grows.
create index if not exists code_nodes_embedding_idx on code_nodes
  using hnsw (embedding extensions.vector_cosine_ops) with (m = 16, ef_construction = 64);

create table if not exists code_edges (
  id             uuid primary key default public.uuid_generate_v7(),
  project_id     uuid not null references projects(id)     on delete cascade,
  repository_id  uuid not null references repositories(id) on delete cascade,
  from_node_id   uuid not null references code_nodes(id)   on delete cascade,
  to_node_id     uuid references code_nodes(id)            on delete cascade,
  to_symbol      text,                              -- unresolved external target
  kind           code_edge_kind not null,
  resolved       boolean not null default true,
  created_at     timestamptz not null default now()
);
-- Both directions: callers of X, and callees of X. One hop each way (ADR-008).
create index if not exists code_edges_from_idx on code_edges (from_node_id, kind);
create index if not exists code_edges_to_idx   on code_edges (to_node_id,   kind);
