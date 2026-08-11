# 02 — System Architecture

---

## 1. Architectural goals and the constraints that shape them

| Goal | Constraint it must respect | Resulting design choice |
|---|---|---|
| Absorb ingestion spikes without dropping errors | A customer incident produces 10k events in 60 s | Ingest writes to durable storage and returns in <50 ms; all processing is asynchronous behind a queue |
| Long-running AI work must survive restarts | A pipeline run takes 60–180 s and spans many external calls | Durable stage-level state machine; every stage is independently resumable and idempotent |
| Executing AI-generated code must not endanger the platform | Arbitrary code execution is the single largest attack surface | Sandbox is a separate, network-isolated, credential-free, ephemeral container with no path back into the platform |
| Cost must be bounded and attributable | LLM and container time both scale with usage | Every LLM call and sandbox run is metered, attributed to a project, and subject to a hard circuit breaker |
| A small team must be able to operate this | No dedicated SRE | Three deployables, one managed database, one managed queue. No Kubernetes, no service mesh |
| Tenant data must never cross tenants | Multi-tenant from day one | Postgres RLS enforced at the database layer, not the application layer |

---

## 2. Component topology

```
                        ┌───────────────────────────────────────────┐
   Customer backend     │              EDGE / CDN (Vercel)          │
   ┌──────────────┐     │  ┌─────────────────────────────────────┐  │
   │ roottrace    │     │  │  Next.js 14 Dashboard (SSR + RSC)   │  │
   │ SDK          │     │  └──────────────┬──────────────────────┘  │
   └──────┬───────┘     └─────────────────┼─────────────────────────┘
          │ HTTPS                          │ HTTPS + WSS
          │ POST /v1/events                │
          ▼                                ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │                    API SERVICE  (FastAPI, stateless, N replicas) │
   │  ┌────────────┐ ┌────────────┐ ┌───────────┐ ┌────────────────┐  │
   │  │ Ingestion  │ │ Dashboard  │ │ WebSocket │ │ GitHub webhook │  │
   │  │ router     │ │ REST API   │ │ hub       │ │ receiver       │  │
   │  └─────┬──────┘ └─────┬──────┘ └─────┬─────┘ └────────┬───────┘  │
   │        │ middleware: authn · tenancy · ratelimit · idempotency   │
   └────────┼──────────────┼──────────────┼────────────────┼──────────┘
            │              │              │                │
            │ enqueue      │ read         │ pub/sub        │ enqueue
            ▼              ▼              ▼                ▼
   ┌─────────────────┐  ┌──────────────────────┐  ┌──────────────────┐
   │  REDIS          │  │  SUPABASE POSTGRES   │  │  OBJECT STORAGE  │
   │  · ARQ queues   │  │  · relational data   │  │  (Supabase       │
   │  · pub/sub      │  │  · pgvector index    │  │   Storage)       │
   │  · rate limit   │  │  · RLS policies      │  │  · raw payloads  │
   │  · idempotency  │  │  · audit log         │  │  · sandbox logs  │
   │  · dedupe locks │  │  · GoTrue auth       │  │  · diffs         │
   └────────┬────────┘  └──────────┬───────────┘  └────────┬─────────┘
            │ dequeue              │ read/write            │ read/write
            ▼                      ▼                       ▼
   ┌──────────────────────────────────────────────────────────────────┐
   │            WORKER SERVICE  (ARQ, M replicas, autoscaled)         │
   │  ┌────────────────────────────────────────────────────────────┐  │
   │  │  PIPELINE ORCHESTRATOR  — durable 14-stage state machine   │  │
   │  └───┬──────────┬──────────┬───────────┬──────────┬──────────┘  │
   │      ▼          ▼          ▼           ▼          ▼             │
   │  ┌────────┐ ┌────────┐ ┌────────┐ ┌─────────┐ ┌──────────┐      │
   │  │Retrieval│ │  LLM   │ │Sandbox │ │ GitHub  │ │ Scoring  │      │
   │  │ engine  │ │gateway │ │ client │ │ client  │ │  engine  │      │
   │  └───┬────┘ └───┬────┘ └───┬────┘ └────┬────┘ └──────────┘      │
   └──────┼──────────┼──────────┼───────────┼──────────────────────────┘
          │          │          │           │
          │          ▼          ▼           ▼
          │   ┌───────────┐ ┌─────────────────────┐ ┌──────────────┐
          │   │ LLM       │ │  SANDBOX RUNNER     │ │  GitHub API  │
          │   │ providers │ │  ephemeral container│ │  (App auth)  │
          │   │ (routed)  │ │  ── NO NETWORK ──   │ │              │
          │   └───────────┘ │  ── NO SECRETS ──   │ └──────────────┘
          │                 │  ── READ-ONLY FS ── │
          └────────────────►└─────────────────────┘
                (file bundle in, results out — over a unix socket only)
```

---

## 3. The four deployables

### 3.1 `api` — FastAPI, stateless

**Responsibilities**

- Terminate HTTPS, authenticate every request (API key for ingest, JWT for dashboard).
- Validate payloads against JSON Schema; reject malformed data at the edge with precise errors.
- Persist the raw event durably, enqueue a job, return `202 Accepted` immediately.
- Serve the dashboard REST API with RLS-scoped queries.
- Host the WebSocket hub for live pipeline updates (subscribes to Redis pub/sub).
- Receive and verify GitHub webhooks (HMAC-SHA256 signature check).

**Explicitly NOT responsible for**

- Any LLM call, any sandbox execution, any long-running work. The API must never block. If a handler can take more than 200 ms, it belongs in the worker.

**Scaling:** horizontally, stateless, behind a load balancer. Target p99 < 100 ms on ingest.

---

### 3.2 `worker` — ARQ (asyncio Redis Queue)

**Responsibilities**

- Execute the 14 pipeline stages.
- Own all outbound integrations: LLM providers, GitHub, sandbox.
- Emit progress events to Redis pub/sub after every stage.
- Enforce token budgets, cost caps, retry policy, and timeouts.

**Queue design**

| Queue | Purpose | Concurrency | Priority |
|---|---|---|---|
| `rt:ingest` | Fingerprint, dedupe, triage. Fast, high volume | 32 | high |
| `rt:pipeline` | The AI pipeline. Slow, expensive | 8 | normal |
| `rt:sandbox` | Container validation runs. Resource-bound | 4 | normal |
| `rt:github` | PR authoring, webhook follow-ups | 8 | normal |
| `rt:maintenance` | Retention, aggregates, reindex | 2 | low |

Separate queues matter: a burst of 10k ingest events must not starve the pipeline, and sandbox concurrency must be capped independently because it is bounded by host CPU, not by API rate limits.

**Scaling:** by queue depth. `rt:ingest` scales aggressively, `rt:sandbox` scales conservatively.

---

### 3.3 `web` — Next.js 14 App Router

- Server Components for data-heavy list and detail views (issue list, log explorer).
- Client Components only where interactivity demands it (pipeline viewer, diff viewer, filters).
- WebSocket client for live pipeline state.
- No direct database access. Every read goes through the `api` service so RLS and audit logging apply uniformly.

---

### 3.4 `sandbox-runner` — the validation container image

Not a long-running service. A container image, launched per validation, destroyed after. Fully specified in `07-SANDBOX-VALIDATION.md`.

Key property: **it has no way to reach anything.** No network namespace with routes, no environment secrets, no volume mounts other than a single read-only input bundle and a write-only results path. Communication with the worker is a unix domain socket handed in at launch.

---

## 4. Data flow — the ingest path in detail

```
1.  SDK          POST /v1/events
                 Headers: Authorization: Bearer rt_live_…
                          Idempotency-Key: <uuid>
                 Body:    { events: [ …≤100 event objects… ] }

2.  api          ├─ resolve API key → project_id, plan, scopes      (Redis cache, 60s TTL)
                 ├─ rate limit check                                (Redis token bucket)
                 ├─ idempotency check                               (Redis SETNX, 24h)
                 ├─ JSON Schema validation                          (fail fast, per-event)
                 ├─ secret-scan + PII redaction on payload          (regex + entropy)
                 ├─ INSERT raw_events (batch)                       (Postgres)
                 ├─ PUT raw payload blob                            (Object Storage)
                 └─ enqueue rt:ingest job per event
                 ◄─ 202 { accepted: 100, rejected: 0, batch_id }
                 Total budget: 50 ms

3.  worker       rt:ingest
    (ingest)     ├─ compute fingerprint                             (deterministic, see 03 §3)
                 ├─ upsert issue, increment occurrence_count        (Postgres, ON CONFLICT)
                 ├─ INSERT error_occurrence                         (Postgres)
                 ├─ evaluate triage rules → severity
                 ├─ dedupe gate: is an investigation already open
                 │  for this fingerprint within the cooldown?
                 │      yes → attach occurrence, stop
                 │      no  → INSERT investigation (status=queued)
                 └─ enqueue rt:pipeline

4.  worker       rt:pipeline → the 14 stages (doc 03)
    (pipeline)   after every stage:
                 ├─ INSERT pipeline_step (durable)
                 └─ PUBLISH rt:inv:<id> on Redis pub/sub

5.  api          WebSocket hub is subscribed to rt:inv:*
                 → pushes frames to any connected dashboard client
                   authorised for that project

6.  web          Pipeline viewer animates the stage transition
```

**Why this shape**

- Ingest is decoupled from processing, so an error storm degrades into queue depth, not dropped data or a 500.
- The raw payload is persisted *before* any processing. If the pipeline has a bug, no customer data is ever lost, and every run is replayable from source.
- Fingerprinting happens in the worker, not the API — it's cheap but not free, and keeping the API path minimal is what holds p99 down.

---

## 5. Technology decisions with justification

### 5.1 Backend: Python 3.12 + FastAPI

| Criterion | Python/FastAPI | Node/NestJS | Go |
|---|---|---|---|
| Multi-language AST tooling (Tree-sitter, static analysis) | **Excellent** — `tree-sitter`, `libcst`, `ruff`, `bandit`, `mypy` all first-class | Weak — would shell out to Python anyway | Weak |
| LLM / embedding ecosystem | **Excellent** | Good | Fair |
| Async I/O performance | Very good (uvloop) | Excellent | Excellent |
| Raw CPU throughput | Fair | Fair | Excellent |
| Type safety | Good (Pydantic v2 + mypy strict) | Excellent | Excellent |
| Team velocity | **High** | High | Medium |

**Chosen: Python.** This system is I/O-bound (LLM calls, GitHub calls, database) — raw CPU is irrelevant. The workload that *is* CPU-bound (AST parsing, static analysis) is precisely where Python's tooling is strongest. Pydantic v2 gives us schema validation, serialisation, and LLM output parsing from one type definition, which is a genuine multiplier for a system whose central risk is malformed structured output.

**Accepted trade-off:** the frontend is TypeScript, so we run two languages. Mitigated by generating both TS and Python types from shared JSON Schemas in `packages/shared-types`.

### 5.2 Database: Supabase (Postgres 17 + pgvector)

| Criterion | Supabase | Self-hosted PG | PG + Pinecone |
|---|---|---|---|
| Relational + vector in one store | ✅ | ✅ | ❌ two stores, two consistency models |
| Auth included (GoTrue, GitHub OAuth) | ✅ | Build it | Build it |
| Row-level security | ✅ native | ✅ native | Partial |
| Realtime subscriptions | ✅ | Build it | Build it |
| Operational burden | **Low** | High | High |
| Vendor lock-in | Low — it *is* Postgres; `pg_dump` and leave | None | Medium |
| Cost at V1 scale | Low | Medium (your time) | Medium+ |

**Chosen: Supabase.** The decisive factor is that RLS gives us database-enforced multi-tenancy. Application-layer tenancy is one forgotten `WHERE project_id = ...` away from a cross-tenant data leak; RLS makes that class of bug structurally impossible. Auth, storage, and pgvector arriving in the same product removes three separate integrations.

**Exit strategy, deliberately preserved:** we use no Supabase-proprietary SQL. Migrations are plain SQL. Auth is standard JWT verified by public key. Moving to any Postgres host is a `pg_dump`/`pg_restore` plus swapping GoTrue for another OIDC provider.

### 5.3 Queue: Redis + ARQ

Considered: Celery, RQ, Dramatiq, ARQ, SQS.

**Chosen: ARQ.** Native asyncio (our entire codebase is async — Celery's async story is still awkward), tiny surface area, built-in job results, deferred jobs, and retries. Celery is more featureful but brings a heavy configuration burden for features we don't need. SQS would add a second cloud dependency and lose us pub/sub, which we already need for WebSocket fan-out.

Redis serves four roles: queue, pub/sub, rate-limit counters, and idempotency/dedupe locks. One system, four jobs, all of them things Redis is genuinely the right tool for.

### 5.4 Frontend: Next.js 14 App Router + Tailwind + shadcn/ui

Server Components let the heavy list views (log explorer with 100k rows, issue list) render on the server with the data already joined, which is the difference between a snappy dashboard and a loading-spinner product. shadcn/ui gives us accessible primitives we own the source of — critical, because our design system (`10`) is a specific light/blue aesthetic, and we need to restyle deeply rather than fight a library's opinions.

### 5.5 Sandbox: Docker with hardened runtime (gVisor where available)

Full justification in `07`. Summary: we need to execute untrusted, AI-generated code. Options were repo CI (no offline story, unusable for V1's fake-data mandate), a third-party sandbox service (adds a vendor in the critical path, and we still own the security story), or our own hardened container. We chose our own, with defence in depth, and we add repo CI as an *additional* gate in V2 rather than a replacement.

---

## 6. Cross-cutting concerns

### 6.1 Multi-tenancy

Every tenant-scoped table carries `project_id uuid not null`. Every such table has RLS enabled with a policy tying access to the caller's project memberships. The application connects as an authenticated role that **cannot bypass RLS**; only migrations run as owner.

```sql
-- the shape every project-scoped tenant table follows
alter table investigations enable row level security;
alter table investigations force  row level security;

create policy tenant_read on investigations for select
  using (project_id in (select rt_auth.project_ids()));

create policy tenant_write on investigations for all
  using      (project_id in (select rt_auth.project_ids())
              and rt_auth.can_write_project(project_id))
  with check (project_id in (select rt_auth.project_ids())
              and rt_auth.can_write_project(project_id));
```

`rt_auth.project_ids()` is a plain `stable` helper running as the caller. Recursion is prevented by the shape of the policies rather than by privilege: membership policies are own-row-only, and `projects` reads them inline. The full model, including the identity tables that need bespoke policies, is in `04` §12.

Workers use a service role, but every worker query still passes `project_id` explicitly and is covered by a test asserting cross-tenant reads return zero rows.

### 6.2 Idempotency

Three independent layers:

1. **HTTP layer** — `Idempotency-Key` header, Redis `SETNX` with 24 h TTL, replays return the original response body and status.
2. **Fingerprint layer** — identical errors collapse into one Issue by deterministic hash.
3. **Stage layer** — each pipeline stage checks whether its output already exists for this investigation before doing work. A worker crash mid-pipeline resumes at the failed stage, never re-runs completed ones. This is what makes the expensive stages safe to retry.

### 6.3 Failure handling

| Failure | Behaviour |
|---|---|
| Transient (429, 502, timeout) | Exponential backoff with jitter: 1s, 2s, 4s, 8s, 16s. Max 5 attempts |
| Permanent (400, schema violation after repair retry) | Fail the stage, mark investigation `failed`, record the reason, no retry |
| Worker crash mid-stage | Job is redelivered by ARQ; stage idempotency check skips completed work |
| LLM provider outage | Gateway fails over to the next provider in the tier |
| Cost cap exceeded | Circuit breaker opens for the project; new investigations queue as `blocked_quota` and surface in the UI |
| Sandbox timeout | Kill container, record as validation failure, feed into repair loop |
| Poison job (5 consecutive failures) | Move to dead-letter queue, alert, never retry automatically |

### 6.4 Configuration & secrets

- All configuration via environment variables, typed and validated by a Pydantic `Settings` class at boot. The process refuses to start on missing or malformed config — no silent defaults for anything security-relevant.
- Runtime secrets (customer LLM keys, GitHub installation tokens) live encrypted in Postgres, envelope-encrypted with a KMS-held data key. Decrypted only in worker memory, at the moment of use, never logged, never written to disk, never passed into a sandbox.
- GitHub installation tokens are minted per-operation with a ~60-minute lifetime and are never persisted.

---

## 7. Deployment topology (V1)

| Component | Platform | Instances | Notes |
|---|---|---|---|
| `web` | Vercel | serverless | Edge cache for static, SSR for dynamic |
| `api` | Railway or Fly.io | 2 (min) | Autoscale on CPU + request rate |
| `worker` | Railway or Fly.io | 2 (min) | Autoscale on queue depth |
| `sandbox-runner` | Fly Machines / dedicated VM pool | on-demand | Isolated from all other workloads |
| Postgres + pgvector | Supabase managed | 1 primary | PITR enabled |
| Redis | Upstash or Railway Redis | 1 | AOF persistence for queue durability |
| Object storage | Supabase Storage | — | Raw payloads, sandbox logs, diffs |

Full detail in `13-DEPLOYMENT.md`.

---

## 8. Scaling path (what we do when, and only when, we need to)

| Load signal | Response |
|---|---|
| Ingest p99 > 150 ms | Add `api` replicas (stateless, trivial) |
| `rt:pipeline` depth > 100 sustained | Add `worker` replicas |
| `rt:sandbox` is the bottleneck | Add dedicated sandbox host capacity; it scales independently by design |
| Postgres CPU > 70% | Add read replica for dashboard queries; keep writes on primary |
| `raw_events` > 100M rows | Partition by month; move partitions older than 90 days to cold storage |
| pgvector recall degrades | Tune HNSW `m`/`ef_search`; only then consider a dedicated vector store |
| Vector index > 50M rows | *Now* evaluate a dedicated vector DB. Not before |

Deliberate philosophy: every entry here is a response to a measured signal. None of it is built in advance.

---

## 9. Latency budget — expected p95, **not** timeouts

> **`03` §6 is canonical** for both p95 targets and hard timeouts. This table reproduces the p95 column for architectural context only. If the two disagree, `03` §6 wins and this table is the defect.

A p95 target and a hard timeout are different things and are never used interchangeably: exceeding the **target** means *slow* and burns SLO budget; exceeding the **timeout** means *failed*.

| Stage | Target p95 | Hard timeout | Dominated by |
|---|---|---|---|
| 1–3 Ingest, fingerprint, triage | 400 ms | 1–2 s | Postgres writes |
| 4 Error understanding | 3 s | 10 s | LLM (fast tier) |
| 5 Context retrieval | 8 s | 20 s | GitHub API (parallelised), pgvector query |
| 6 Root-cause reasoning | 25 s | 60 s | LLM (reasoning tier, longest single call) |
| 7 Patch generation | 15 s | 45 s | LLM |
| 8 Sandbox validation | 45 s | **90 s** | Container start + deps + **two** test passes + **two** static passes |
| 9 Repair loop (if triggered) | +60 s per attempt | — | Repeat of 7+8 |
| 10 Critic review | 12 s | 30 s | LLM |
| 11 Confidence scoring | 200 ms | 1 s | Pure computation |
| 12 PR authoring | 4 s | 20 s | GitHub API |
| **Total, happy path** | **≈ 115 s** | 300 s | |
| **Total, one repair attempt** | **≈ 175 s** | 460 s | |

Stage 8's hard timeout is 90 s rather than 45 s because G6 and G7 each run **twice** — a pre-patch baseline and a post-patch run — since only *newly* failing tests and *new* static findings count against a patch. See B11 in `07` §L6.

The dashboard streams every transition, so perceived latency is far lower than wall-clock — the user watches progress rather than a spinner. This is a deliberate UX decision that lets us spend real time on quality.

---

*Next: [`03-PIPELINE-SPEC.md`](./03-PIPELINE-SPEC.md) — the core document.*
