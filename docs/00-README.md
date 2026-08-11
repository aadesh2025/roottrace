# RootTrace AI — Engineering Documentation

> **RootTrace AI** is an autonomous API error observatory. It ingests production errors from your backend, retrieves the exact code context responsible from your GitHub repository, reasons about root cause with a multi-stage AI engine, generates a patch, **proves the patch works inside our own sandbox compiler**, and only then opens a pull request — with a full, inspectable audit trail of every step.

**Document set version:** 1.0
**Target release:** V1 (Pipeline Proving Release, fake-data driven)
**Status:** Design-complete, implementation-ready

---

## 1. Who this documentation is for

| Reader | Start here | Then read |
|---|---|---|
| New backend engineer | `02`, `03`, `04`, `05` | `06`, `07` |
| New frontend engineer | `09`, `10`, `05` | `03` (to understand what you're visualising) |
| AI / ML engineer | `06`, `03`, `A2` | `07`, `14` |
| DevOps / SRE | `13`, `12`, `11` | `07` |
| Security reviewer | `11`, `07`, `04` (RLS section) | `08` |
| Founder / PM | `01`, `16` | `15` |
| Anyone building V1 today | **`15`** | everything it references |

---

## 2. Document index

### Core

| # | File | What it answers |
|---|---|---|
| 00 | `00-README.md` | This file. Map of everything. |
| 01 | `01-PRODUCT-SPEC.md` | What we're building, for whom, why, and explicitly what we are *not* building. Full feature catalogue. |
| 02 | `02-SYSTEM-ARCHITECTURE.md` | Services, boundaries, data flow, deployment topology, technology decisions with justification. |
| 03 | `03-PIPELINE-SPEC.md` | **The most important document.** Every one of the 14 pipeline stages: inputs, outputs, JSON contracts, failure modes, retries, state machine, repair loop. |
| 04 | `04-DATA-MODEL.md` | Complete Postgres/Supabase schema, DDL, indexes, RLS policies, migrations, retention. |
| 05 | `05-API-SPEC.md` | Every HTTP endpoint and WebSocket channel: auth, request, response, error codes, rate limits, idempotency. |
| 06 | `06-AI-ENGINE.md` | Model routing, prompt architecture, reasoning chain, critic pass, confidence scoring maths, hallucination guardrails, cost control. |
| 07 | `07-SANDBOX-VALIDATION.md` | The in-app sandbox compiler: container design, escape prevention, resource limits, language runners, the failure→repair loop. |
| 08 | `08-GITHUB-INTEGRATION.md` | GitHub App, webhooks, selective file retrieval (never full clone), branch/commit/PR via Git Data API, check polling. |
| 09 | `09-FRONTEND-DASHBOARD.md` | Every page, every panel, every state. Live pipeline viewer, log explorer, evidence panel, diff viewer. |
| 10 | `10-DESIGN-SYSTEM.md` | Light premium theme. Colour tokens (white/blue only), typography, spacing, elevation, component specs, motion, accessibility. |
| 11 | `11-SECURITY.md` | Threat model (STRIDE), authn/authz, secret handling, prompt-injection defence, sandbox isolation, OWASP mapping, compliance posture. |
| 12 | `12-OBSERVABILITY.md` | Structured logging, metrics catalogue, tracing, alerting, per-tenant cost accounting. |
| 13 | `13-DEPLOYMENT.md` | Environments, IaC, container images, CI/CD, migrations, rollback, DR. |
| 14 | `14-TESTING.md` | Test pyramid, fixtures, golden pipeline tests, LLM evaluation harness, load tests, security tests. |
| 15 | `15-V1-BUILD-PLAN.md` | **Build this first.** Week-by-week, ticket-by-ticket plan for the fake-data pipeline release. Acceptance criteria per step. |
| 16 | `16-ROADMAP.md` | V2–V6. AI chat, multi-model, learning loop, team features, marketplace. |
| 17 | `17-GLOSSARY.md` | Terminology and canonical error-code registry. |
| 18 | `18-CANONICAL-REGISTRY.md` | **Single source of truth for every duplicated value.** Gate count, stage timings, table counts, roadmap versions, fixture values. Plus the Phase 0 repair log. Check it before restating any number. |

### Appendices

| # | File | Contents |
|---|---|---|
| A1 | `appendix/A1-FAKE-DATA-FIXTURES.md` | The synthetic repository, synthetic error corpus, and expected outputs used to prove V1. |
| A2 | `appendix/A2-PROMPT-LIBRARY.md` | Every production prompt, verbatim, with output schemas and versioning rules. |
| A3 | `appendix/A3-CONFIGURATION.md` | Every environment variable, feature flag, and per-project setting. |
| A4 | `appendix/A4-ADR-LOG.md` | Architecture Decision Records — the eight decisions that shape this system and why. |

---

## 3. The system in sixty seconds

```
   Customer backend                    RootTrace AI                        GitHub
 ┌──────────────────┐          ┌───────────────────────────┐         ┌──────────────┐
 │  API throws 500  │          │                           │         │              │
 │        │         │  POST    │  1  Ingest + validate     │         │              │
 │        └─────────┼─────────►│  2  Fingerprint + dedupe  │         │              │
 │   roottrace SDK  │  /v1/    │  3  Triage + enqueue      │         │              │
 └──────────────────┘  events  │  4  Error understanding   │         │              │
                               │  5  Context retrieval ────┼────────►│ selective    │
                               │  6  Root-cause reasoning  │◄────────┤ file fetch   │
                               │  7  Patch generation      │         │              │
                               │  8  SANDBOX VALIDATION ◄┐ │         │              │
                               │  9  Repair loop ────────┘ │         │              │
                               │ 10  Critic review         │         │              │
                               │ 11  Confidence scoring    │         │              │
                               │ 12  Branch + commit + PR ─┼────────►│ Pull Request │
                               │ 13  Human decision        │◄────────┤              │
                               │ 14  Feedback capture      │         │              │
                               └───────────┬───────────────┘         └──────────────┘
                                           │
                                    ┌──────▼──────┐
                                    │  Dashboard  │  live pipeline view, logs,
                                    │  (Next.js)  │  evidence, diff, history
                                    └─────────────┘
```

---

## 4. The three non-negotiable engineering principles

These appear repeatedly across the docs. If a design choice conflicts with one of them, the design choice is wrong.

### P1 — Nothing reaches a human without proof

An AI-generated patch is a **hypothesis**, not an answer. It becomes a proposal only after it survives: syntax parse → dependency resolution → build → test suite → static analysis → independent critic review. If any hard gate fails, the patch re-enters the repair loop or the investigation is closed as `unresolved`. We never open a PR from an unvalidated hypothesis.

### P2 — Every claim carries its evidence

Every root cause statement is bound to a concrete artefact: a file path, a line range, a stack frame, a commit SHA, a test name. The dashboard renders that binding. A user must always be able to click any assertion the AI makes and see the exact source material behind it. Assertions without evidence are dropped by the validator before they ever reach the UI.

### P3 — Retrieve narrowly, never wholesale

We never clone a repository and we never feed a codebase to a model. We resolve stack frames to file paths, fetch **only** those files plus their direct call-graph neighbours, and enforce a hard token budget. This is simultaneously the cost strategy, the latency strategy, the accuracy strategy, and the security strategy — a smaller context means less to leak, less to hallucinate over, and less to pay for.

---

## 5. Decisions already locked

These were resolved before writing and are treated as fixed across the entire doc set.

| Decision | Choice | Documented in |
|---|---|---|
| Validation gate for V1 | **In-app sandbox compiler**; repo CI added in V2 as a second gate | `07`, `A4-ADR-001` |
| Backend language | **Python 3.12 + FastAPI**; workers via ARQ + Redis | `02`, `A4-ADR-002` |
| Database + Auth | **Supabase** (Postgres 15 + pgvector + GoTrue Auth + RLS + Storage) | `04`, `A4-ADR-003` |
| Repo access | **GitHub App**, selective file fetch via Contents/Git Data API — never a clone | `08`, `A4-ADR-004` |
| Frontend | **Next.js 14 App Router**, TypeScript, Tailwind, shadcn/ui, Monaco, Recharts | `09`, `A4-ADR-005` |
| Theme | **Light only.** White surfaces, blue accent. No dark mode in V1, no orange/green in the brand palette | `10`, `A4-ADR-006` |
| V1 scope | **Pipeline only**, driven end-to-end by fake data. No real customer repos until the pipeline is proven | `15`, `A4-ADR-007` |
| Vector store | **pgvector inside Supabase**, not a dedicated vector DB | `04`, `A4-ADR-008` |

---

## 6. Repository layout this documentation describes

```
roottrace/
├─ apps/
│  ├─ api/                     # FastAPI — HTTP + WebSocket surface
│  │  ├─ roottrace_api/
│  │  │  ├─ main.py
│  │  │  ├─ routers/           # one module per resource
│  │  │  ├─ schemas/           # Pydantic request/response models
│  │  │  ├─ deps/              # auth, db session, rate limit, tenancy
│  │  │  └─ middleware/
│  │  └─ tests/
│  ├─ worker/                  # ARQ workers — the pipeline lives here
│  │  ├─ roottrace_worker/
│  │  │  ├─ pipeline/          # one module per stage (see doc 03)
│  │  │  ├─ ai/                # LLM gateway, prompts, parsers, scoring
│  │  │  ├─ sandbox/           # container orchestration + language runners
│  │  │  ├─ github/            # App auth, retrieval, PR authoring
│  │  │  └─ retrieval/         # frame resolution, graph walk, vector search
│  │  └─ tests/
│  ├─ web/                     # Next.js dashboard
│  │  ├─ app/
│  │  ├─ components/
│  │  ├─ lib/
│  │  └─ styles/
│  └─ sandbox-runner/          # the container image executed per validation
├─ packages/
│  ├─ shared-types/            # JSON Schemas → generated TS + Python types
│  └─ sdk-python/              # roottrace client SDK shipped to customers
├─ infra/
│  ├─ terraform/
│  ├─ docker/
│  └─ supabase/migrations/
├─ fixtures/                   # V1 fake data (see appendix A1)
│  ├─ synthetic-repo/
│  └─ error-corpus/
└─ docs/                       # ← you are here
```

---

## 7. How to use this documentation while building

1. Read `01` and `02` once, fully. Do not skip to code.
2. Read `03` twice. Every stage contract in it is a real interface you will implement literally.
3. Apply the migrations from `04` before writing any application code.
4. Implement `15` step by step. Each step has acceptance criteria; do not advance until they pass.
5. When you change a contract, change `03` and `04` in the same commit. **Documentation drift is a defect.**

---

## 8. Conventions used throughout

- `snake_case` for all database identifiers, JSON keys, and Python symbols.
- `camelCase` only at the TypeScript/React boundary; conversion happens once, in `apps/web/lib/api-client.ts`.
- All IDs are UUIDv7 (time-sortable), prefixed on display only: `inv_01H…`, `iss_01H…`.
- All timestamps are `timestamptz`, stored UTC, ISO-8601 in JSON.
- All money and token counts are integers (micro-USD, whole tokens) — never floats.
- Every pipeline stage emits exactly one `pipeline_step` row and one WebSocket frame.
- Error codes follow `RT-<DOMAIN>-<NNNN>` and are registered centrally in `17-GLOSSARY.md`.

---

## 9. What "done" means for V1

V1 is complete when, with **zero real customer data**:

- [ ] A fake error posted to `/v1/events` traverses all 14 stages without manual intervention.
- [ ] The dashboard renders the run live, stage by stage, and lets you open any stage.
- [ ] The AI produces a root cause bound to real evidence from the synthetic repo.
- [ ] The generated patch is compiled and tested inside our sandbox.
- [ ] A deliberately-broken patch triggers the repair loop and the second attempt succeeds.
- [ ] A confidence score is computed from real signals and displayed with its breakdown.
- [ ] A simulated PR record is created and shown with full description.
- [ ] Every run is persisted per user with complete history and is replayable.
- [ ] `pytest` green, `playwright` green, no `CRITICAL` findings from the security checklist in `11`.

---

*Next: read [`01-PRODUCT-SPEC.md`](./01-PRODUCT-SPEC.md).*
