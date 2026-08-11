# RootTrace AI — working agreement

Autonomous API error observatory. Production error in → targeted code retrieval from GitHub → root-cause reasoning → patch → **proved in our own sandbox** → pull request, with a full audit trail.

You are building **V1: the pipeline, on fake data, nothing else.**

---

## Read before writing any code

`docs/` is the specification and it is **binding**, not advisory.

| Doc | When you need it |
|---|---|
| `docs/15-V1-BUILD-PLAN.md` | **Always.** This is the build order and the acceptance criteria |
| `docs/03-PIPELINE-SPEC.md` | Any pipeline stage. The JSON contracts are literal — implement them exactly |
| `docs/04-DATA-MODEL.md` | Any schema, migration, or query. Authoritative |
| `docs/05-API-SPEC.md` | Any endpoint or WebSocket frame. Frozen |
| `docs/06-AI-ENGINE.md` + `docs/appendix/A2-PROMPT-LIBRARY.md` | Any LLM call |
| `docs/07-SANDBOX-VALIDATION.md` | Sandbox work. Read the whole thing before touching it |
| `docs/09` + `docs/10` | Any UI |
| `docs/11-SECURITY.md` | Auth, secrets, tenancy, anything touching untrusted input |
| `docs/appendix/A3-CONFIGURATION.md` | Any env var or feature flag |
| `docs/appendix/A4-ADR-LOG.md` | Before proposing an architecture change — the decision may already be made and reasoned |

If the spec and the code disagree, **the spec wins** unless you can argue the spec is wrong — in which case say so and update the doc.

---

## The three principles

Every design choice must satisfy these. If a choice conflicts with one, the choice is wrong.

**P1 — Nothing reaches a human without proof.** A patch is a hypothesis until it survives all nine sandbox gates plus an independent critic. No shortcuts around the gate stack.

**P2 — Every claim carries its evidence.** Every AI assertion binds to a file path, line range, commit SHA, or breadcrumb index, verified by literal string comparison. Unbound claims are discarded before the UI.

**P3 — Retrieve narrowly, never wholesale.** Never clone a repository. Hard 24,000-token context budget, priority-ordered eviction. This is the cost, latency, accuracy, and security strategy simultaneously.

---

## Build order — do not skip ahead

```
W1 foundation → W2 ingest → W3 fixtures → W4 retrieval → W5 reasoning
→ W6 sandbox → W7 loop+score → W8 publish+viewer → W9 dashboard → W10 harden
```

Hard sequencing rules from `15` §14:

- Schema and RLS before any application code. Retrofitting tenancy is a rewrite.
- Fixtures before the pipeline. You cannot test a pipeline without known-good input.
- Retrieval before reasoning. A reasoning stage fed bad context teaches you nothing.
- Sandbox before the repair loop. The loop is driven entirely by sandbox output.
- Scoring before publishing. Publishing is gated on the score.

**The rule that matters most: do not advance past Week 4 until retrieval is genuinely good on all 25 fixtures.** Everything downstream inherits its errors, and a confident wrong answer built on wrong context passes every later gate.

Finish a ticket's acceptance criteria before starting the next. If you think a step should be reordered, say so and wait — don't reorder silently.

---

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12 · FastAPI · ARQ workers · `uv` |
| Data | Supabase — Postgres 17 · pgvector · GoTrue · RLS · Storage |
| Queue | Redis (queue, pub/sub, rate limits, idempotency) |
| Frontend | Next.js 14 App Router · TypeScript · Tailwind · shadcn/ui · Monaco · Recharts · `pnpm` |
| Sandbox | Hardened Docker (+gVisor in prod) |
| Testing | pytest · Playwright · testcontainers |

Do not introduce a new dependency, service, or pattern without saying why and getting agreement. Check `A4-ADR-LOG.md` first — eight major decisions are already made and reasoned.

---

## Conventions

- `snake_case` everywhere. `camelCase` only past the TS boundary, converted in exactly one place: `apps/web/lib/api-client.ts`.
- UUIDv7 for all IDs. Prefixed on display only (`inv_`, `iss_`, `prj_`).
- All timestamps `timestamptz`, stored UTC, ISO-8601 in JSON.
- Money and tokens are **integers** — micro-USD and whole tokens. Never floats.
- Every pipeline stage writes exactly one `pipeline_steps` row and publishes one WebSocket frame.
- Error codes follow `RT-<DOMAIN>-<NNNN>`, registered in `docs/17-GLOSSARY.md`. Don't invent unregistered codes.
- Two numbering systems, deliberately distinct: sandbox gates are **G0–G8**, hallucination guardrails are **H1–H13**. Don't mix them.

---

## Non-negotiables

**Tenancy.** Every tenant table has `project_id` with RLS `enabled` *and* `forced`. Workers run as `service_role` and therefore bypass RLS — so every worker query goes through `TenantRepository`, which raises if built without an explicit `project_id`. Never query a tenant table directly from a worker.

**Secrets.** Never logged, never in a prompt, never in a sandbox environment, never in `localStorage`. The `api` service must not hold the Supabase service-role key — there is a boot assertion for this.

**Sandbox.** No network. No credentials. Read-only rootfs. Non-root. All capabilities dropped. If you find yourself relaxing any of these to make something work, stop and raise it.

**Untrusted input.** Error payloads, source code, commit messages, and model output are all hostile by default. Fence them, validate them, never execute them outside the sandbox.

---

## Testing standard

The same bar we hold the AI to in gate G4:

> **Every bug fix ships with a test that fails before the fix.**

A test that passes both before and after proves nothing.

Also: no `sleep()` in tests. Tests pass in any order and in parallel. A flaky test is a bug — fix it or delete it, never skip it.

Coverage floors: pipeline stages ≥90%, fingerprint/retrieval/scoring ≥95%, auth/RLS ≥95%, overall ≥85%.

---

## Design rules (UI work)

Light theme only. Blue is the only brand colour. Colour means status, never decoration. All numerals `tabular-nums`. Every list has a designed empty state. Loading uses skeletons at real content dimensions, never spinners. One primary button per view.

Green and red appear in exactly two places: diffs and status indicators. The sandbox console is deliberately dark. Everything else is white, near-white, and blue.

Full tokens and component specs in `docs/10-DESIGN-SYSTEM.md`. Never hardcode a colour that isn't a token.

---

## V1 boundaries

**In:** all 14 stages · Supabase schema + RLS + auth · ingest API + Python SDK · full dashboard · sandbox with all 9 gates · GitHub client in `fixture` mode · 25-case eval harness · observability.

**Out:** live GitHub repos · repo indexing/embeddings (schema exists, stays empty) · AI chat · multi-model consensus · auto-merge · billing · team invites · Sentry/Datadog adapters · Node SDK · dark mode.

`GITHUB_MODE=fixture` throughout V1. The client is fully implemented; V2 flips a config value, not code.

---

## Documentation drift is a defect

Change a contract → update the doc **in the same commit**. `docs/` is the shared memory for every future session. If it drifts, the next session builds the wrong thing.

---

## How to work with me

- Show the plan before large changes. I'd rather correct a plan than a thousand lines.
- Ask when the spec is ambiguous. Don't guess and don't paper over it — a wrong guess propagates.
- Push back if something in the docs looks wrong. I wrote them; they contain mistakes.
- Don't mark a ticket done unless its stated acceptance criteria actually pass.
- Prefer boring, obvious code. This system's value is correctness, not cleverness.
