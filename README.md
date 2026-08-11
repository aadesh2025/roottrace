# RootTrace AI

**The API Error Observatory** — autonomous production error diagnosis, with proof.

RootTrace AI ingests errors from your backend, retrieves the exact code responsible from GitHub, reasons about root cause with a multi-stage AI engine, generates a patch, **proves it works inside our own sandbox compiler**, and only then opens a pull request — with a complete, inspectable audit trail of every step.

---

## Start here

📖 **[Full documentation →](./docs/00-README.md)**

| If you are… | Read |
|---|---|
| Building V1 today | [`15-V1-BUILD-PLAN.md`](./docs/15-V1-BUILD-PLAN.md) |
| Understanding the system | [`02-SYSTEM-ARCHITECTURE.md`](./docs/02-SYSTEM-ARCHITECTURE.md) → [`03-PIPELINE-SPEC.md`](./docs/03-PIPELINE-SPEC.md) |
| Working on the backend | [`03`](./docs/03-PIPELINE-SPEC.md), [`04`](./docs/04-DATA-MODEL.md), [`05`](./docs/05-API-SPEC.md) |
| Working on the frontend | [`09`](./docs/09-FRONTEND-DASHBOARD.md), [`10`](./docs/10-DESIGN-SYSTEM.md) |
| Working on the AI engine | [`06`](./docs/06-AI-ENGINE.md), [`A2`](./docs/appendix/A2-PROMPT-LIBRARY.md) |
| Reviewing security | [`11`](./docs/11-SECURITY.md), [`07`](./docs/07-SANDBOX-VALIDATION.md) |
| Asking "why is it built this way?" | [`A4-ADR-LOG.md`](./docs/appendix/A4-ADR-LOG.md) |

---

## Working on it

```bash
make bootstrap    # once per clone: uv sync · pnpm install · pre-commit install
make check        # fmt-check → lint → typecheck → test-unit. The pre-push gate
make ci           # everything CI runs, in CI order
make help         # every target
```

`make check` and the CI `check` job run the identical target set, so a build
that passes locally passes CI by construction. Target list is canonical in
`docs/appendix/A3-CONFIGURATION.md` §5.2.

**Prerequisites:** `uv` · Node 22.13+ with `corepack enable pnpm` · GNU Make ·
`gitleaks` (the pre-commit secret scan fails loudly without it) · **Docker from
T1.2** — the Supabase CLI runs the whole local stack in containers, so
`supabase start` and `supabase db reset` both need the daemon.

**On Windows, run `make` from Git Bash, not PowerShell** — it needs `sh.exe` on
PATH, and from Git Bash recipes behave exactly as they do in CI.

Targets belonging to a later phase fail with the ticket that enables them
rather than succeeding quietly. That is deliberate: a green no-op is how a
missing gate goes unnoticed.

---

## The pipeline

```
error in ──► fingerprint ──► triage ──► understand ──► retrieve ──► reason
                                                                      │
                              ┌───────────────────────────────────────┘
                              ▼
                           patch ──► SANDBOX VALIDATE ──┐
                              ▲              │           │
                              └── repair ◄───┘ fail      │ pass
                                 (max 3)                 ▼
                                                    critique ──► score ──► PR out
```

14 stages. Every one durable, idempotent, resumable, budgeted, and visible live in the dashboard.

---

## Three principles

**P1 — Nothing reaches a human without proof.** A patch is a hypothesis until it survives syntax → dependencies → build → regression test (which must fail on the unpatched code) → existing suite → static analysis → security scan → independent critic review.

**P2 — Every claim carries its evidence.** Every root-cause statement binds to a file path, line range, stack frame, or commit SHA — verified by literal comparison against retrieved source. Unbound claims are discarded before a user ever sees them.

**P3 — Retrieve narrowly, never wholesale.** We never clone a repository. We resolve stack frames to paths, fetch only those files plus their call-graph neighbours, and enforce a hard 24,000-token budget. This is the cost strategy, the latency strategy, the accuracy strategy, and the security story, in one decision.

---

## Stack

| Layer | Choice |
|---|---|
| Backend | Python 3.12 · FastAPI · ARQ workers |
| Data & auth | Supabase — Postgres 17 · pgvector · GoTrue · RLS · Storage |
| Queue | Redis |
| Frontend | Next.js 14 App Router · TypeScript · Tailwind · shadcn/ui · Monaco · Recharts |
| Sandbox | Hardened Docker + gVisor — no network, no credentials, read-only rootfs |
| Repo access | GitHub App · selective fetch · Git Data API |
| Theme | Light only. White surfaces, blue accent |

---

## V1 scope

> **V1 proves the pipeline, not the product.**

V1 runs entirely on a synthetic repository and a 25-case error corpus with known ground truth. No customer repository is connected. The GitHub client is fully implemented and runs in `fixture` mode; V2 flips a config value.

A V1 that runs 14 stages flawlessly on fake data is worth more than a V1 that half-works on real data — because every subsequent phase assumes the pipeline is correct.

**Done when:** a fake error traverses all 14 stages unattended · the dashboard renders it live and every stage is inspectable · root cause is bound to real evidence · the patch compiles and passes tests in our sandbox · a deliberately-broken patch triggers repair and succeeds on attempt 2 · confidence is computed from real signals with a visible breakdown · the two unfixable fixtures terminate honestly as `insufficient_context`.

---

## Documentation map

```
docs/
├─ 00-README.md ················ index and reading order
├─ 01-PRODUCT-SPEC.md ·········· what, for whom, and what we deliberately don't build
├─ 02-SYSTEM-ARCHITECTURE.md ··· components, data flow, technology decisions
├─ 03-PIPELINE-SPEC.md ········· ★ all 14 stages, contracts, state machine, repair loop
├─ 04-DATA-MODEL.md ············ full schema, DDL, RLS, retention
├─ 05-API-SPEC.md ·············· every endpoint and WebSocket channel
├─ 06-AI-ENGINE.md ············· model routing, prompts, guardrails, confidence maths
├─ 07-SANDBOX-VALIDATION.md ···· the in-app compiler and its 8 isolation layers
├─ 08-GITHUB-INTEGRATION.md ···· selective retrieval, PR authoring, webhooks
├─ 09-FRONTEND-DASHBOARD.md ···· every page, panel, and state
├─ 10-DESIGN-SYSTEM.md ········· tokens, components, motion, accessibility
├─ 11-SECURITY.md ·············· threat model, secrets, prompt injection, OWASP
├─ 12-OBSERVABILITY.md ········· logs, metrics, traces, cost accounting
├─ 13-DEPLOYMENT.md ············ environments, IaC, CI/CD, rollback, DR
├─ 14-TESTING.md ··············· test pyramid, AI evaluation harness, acceptance
├─ 15-V1-BUILD-PLAN.md ········· ★ 10 weeks, ticket by ticket
├─ 16-ROADMAP.md ··············· V2–V6
├─ 17-GLOSSARY.md ·············· terms and the error code registry
└─ appendix/
   ├─ A1-FAKE-DATA-FIXTURES.md · the synthetic repo and 25-case corpus
   ├─ A2-PROMPT-LIBRARY.md ····· every production prompt, verbatim
   ├─ A3-CONFIGURATION.md ······ every env var, flag, and project setting
   └─ A4-ADR-LOG.md ············ the 8 decisions that shape the system
```

---

*22 documents · ~10,200 lines · design-complete, implementation-ready.*
