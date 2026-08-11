# 01 — Product Specification

**RootTrace AI — The API Error Observatory**

---

## 1. Executive summary

RootTrace AI closes the loop between "an API threw an error in production" and "a reviewed, tested fix is waiting in a pull request."

Existing observability tools (Sentry, Datadog, New Relic) are excellent at telling you **that** something broke and **where** the stack trace points. They stop there. The remaining 90% of the work — reading the code, forming a hypothesis, writing the fix, running the tests, opening the PR — is still entirely human, and it is where the hours go.

RootTrace AI performs that remaining 90% autonomously, and — critically — **proves its work before asking for your attention.** Every proposal arrives with a compiled, tested patch, a root cause bound to concrete evidence, an independent critic's review, and a numeric confidence score derived from real signals rather than model self-report.

---

## 2. The problem, precisely stated

### 2.1 What actually happens today

```
09:14  Alert fires. Sentry: TypeError in /api/v2/checkout
09:16  On-call engineer acknowledges
09:16–09:40  Reads the stack trace, opens the repo, hunts for the file
09:40–10:25  Reads surrounding code, forms hypothesis, checks git blame
10:25–10:50  Writes the fix
10:50–11:10  Runs tests locally, fixes a broken test
11:10–11:20  Opens PR, writes description
11:20–14:00  Waits for review
```

Roughly **two hours of engineer time** for a bug whose fix was four lines. The diagnostic phase — 09:16 to 10:25 — is nearly all of it, and nearly all of it is mechanical context assembly.

### 2.2 Why this is worth automating

| Dimension | Detail |
|---|---|
| Frequency | A mid-size product ships 5–40 distinct production error signatures per week |
| Cost per incident | 1–3 engineer-hours for diagnosis + fix on well-scoped bugs |
| Repetition | 60–70% of error volume comes from a small number of recurring signatures |
| Context loss | The engineer who fixes it is often not the one who wrote it |
| Night/weekend tax | Errors don't respect working hours; context assembly at 03:00 is worst-case |

### 2.3 Why nobody has fully solved it

- **Naive approach:** dump the repo into an LLM. Fails on cost, context window, latency, and accuracy simultaneously.
- **Copilot-style approach:** requires a human already sitting in the file. Doesn't help with "where is the file."
- **The real difficulty is retrieval, not generation.** Modern models write correct patches when they have correct context. The engineering problem is: *given a stack trace, assemble exactly the right 8,000 tokens of code.*

RootTrace AI is fundamentally **a retrieval and verification system that happens to call an LLM in the middle**, not an LLM wrapper.

---

## 3. Users and jobs to be done

### 3.1 Primary persona — Priya, Backend Lead at a 25-person SaaS

- Owns three services, on-call one week in three.
- **Job:** "When something breaks at 2am, I want to wake up to a tested fix I can review in five minutes, not a stack trace I have to investigate for two hours."
- **Success:** time-to-first-credible-hypothesis drops from ~50 minutes to under 3 minutes.
- **Fear:** an AI silently pushing wrong code into production.
- **Design consequence:** human approval is the default; auto-merge is opt-in per repo *and* per path glob; the PR shows exactly what was tested.

### 3.2 Secondary persona — Dev, solo founder

- Ships fast, has minimal test coverage, no dedicated ops.
- **Job:** "I don't have time to investigate. Tell me what broke and hand me a fix."
- **Design consequence:** the product must be useful with weak test suites — hence sandbox validation with our own generated regression test, not sole reliance on their CI.

### 3.3 Tertiary persona — Anika, Platform/SRE at a 200-person company

- **Job:** "Show me error *trends*, not individual errors. Which signature is burning the most engineer time? Which service is degrading?"
- **Design consequence:** analytics, repeat-occurrence tracking, and per-service health scoring are first-class dashboard features, not an afterthought.

---

## 4. Product principles

| # | Principle | Consequence in the build |
|---|---|---|
| 1 | **Proof over plausibility** | Sandbox validation is a hard gate. No PR without a green run. |
| 2 | **Evidence over assertion** | Every claim links to a file, line range, frame, or SHA. Unbound claims are stripped by the output validator. |
| 3 | **Glass box, not black box** | The full pipeline is inspectable: prompts sent, context retrieved, tokens spent, sandbox stdout. |
| 4 | **Narrow retrieval** | Fetch only what the trace and call graph justify. Hard token budget, enforced. |
| 5 | **Human keeps the keys** | Approval by default. Autonomy is earned per-repo and revocable. |
| 6 | **Calm, quiet interface** | Light theme, generous whitespace, blue-only accent. Colour is reserved to mean *status*, never decoration. |
| 7 | **Every run is replayable** | Full history persisted per user. Any investigation can be re-run against a new model or prompt version. |

---

## 5. Feature catalogue

Legend: **V1** = the Pipeline Proving Release (fake data). **V2+** = later phases, specified in `16-ROADMAP.md`.

### 5.1 Ingestion & error management

| Feature | Description | Phase |
|---|---|---|
| Ingest API | `POST /v1/events` — authenticated, idempotent, batched, schema-validated | V1 |
| Python SDK | Drop-in exception hook + FastAPI/Django/Flask middleware | V1 |
| Node SDK | Express/Nest middleware + `process.on('uncaughtException')` | V2 |
| Fingerprinting | Deterministic hash grouping identical errors into one Issue | V1 |
| Repeat-occurrence tracking | Count, first seen, last seen, per-hour rate, affected environments | V1 |
| Severity triage | Rules engine: rate × affected users × endpoint criticality → P0–P3 | V1 |
| Manual submission | Paste a stack trace into the dashboard to trigger an investigation | V1 |
| Source adapters | Sentry, Datadog, OpenTelemetry, generic webhook | V2 |
| Noise suppression | Mute a signature, snooze until rate exceeds threshold | V2 |
| Alert routing | Slack, email, PagerDuty on state transitions | V2 |

### 5.2 The investigation pipeline

| Feature | Description | Phase |
|---|---|---|
| 14-stage orchestrated pipeline | Durable, resumable, idempotent per stage | V1 |
| Live pipeline visualisation | Real-time stage-by-stage progress over WebSocket | V1 |
| Stage drill-down | Click any stage → inputs, outputs, duration, tokens, cost, raw payloads | V1 |
| Frame resolution | Map stack frames to repo file paths, handling monorepos and path rewriting | V1 |
| Selective file retrieval | Fetch only implicated files + neighbours. Never a clone | V1 |
| Call-graph expansion | Tree-sitter AST → callers/callees of the implicated function | V1 |
| Vector similarity retrieval | pgvector search over function-level code embeddings | V1 |
| Git history context | `git blame` on implicated lines, recent commits touching those files | V1 |
| Test discovery | Locate existing tests covering the implicated code | V1 |
| Token budgeting | Hard cap per stage with priority-ordered eviction | V1 |
| Pipeline replay | Re-run any historical investigation under a new model/prompt version | V1 |

### 5.3 AI reasoning engine

| Feature | Description | Phase |
|---|---|---|
| Error understanding | Structured extraction: exception type, frames, endpoint, params, env | V1 |
| Multi-step root-cause chain | Observation → hypotheses → evidence test → conclusion (why → why) | V1 |
| Structured JSON output | Strict schema, validated; retry with repair prompt on violation | V1 |
| Evidence binding | Every claim must cite a retrieved artefact or is discarded | V1 |
| Patch generation | Unified diff, scoped to identified files only | V1 |
| Independent critic pass | Separate call, fresh context, adversarial persona | V1 |
| Composite confidence score | Weighted from build, tests, static analysis, critic, retrieval quality | V1 |
| Model routing | Per-stage model selection with cost/latency/quality tiers | V1 |
| Multi-model consensus | N models on the same stage, agreement raises confidence | V2 |
| Hallucination guardrails | Path existence checks, symbol existence checks, diff applicability check | V1 |
| Prompt versioning | Every prompt versioned; runs record which version produced them | V1 |
| Cost ledger | Per-call token + micro-USD accounting, per project and per user | V1 |
| AI chat over an investigation | Ask follow-up questions about a specific run | V4 |
| Learned retrieval weighting | Merge/reject feedback tunes retrieval ranking | V3 |

### 5.4 Sandbox validation

| Feature | Description | Phase |
|---|---|---|
| In-app sandbox compiler | Isolated container, no network, no credentials, hard resource caps | V1 |
| Syntax + parse gate | Fast fail before spending a container | V1 |
| Dependency resolution | Offline install from a pre-warmed wheel/module cache | V1 |
| Build/compile step | Language-appropriate compile or import check | V1 |
| Test execution | Run discovered tests plus AI-generated regression test | V1 |
| Static analysis | ruff/mypy/bandit (Python), eslint/tsc (JS/TS) | V1 |
| Repair loop | On failure, feed sandbox stderr back to stage 4; bounded attempts | V1 |
| Full sandbox transcript | stdout/stderr/exit codes stored and shown in the UI | V1 |
| Repo CI as second gate | Poll GitHub Checks after PR creation | V2 |
| Multi-language runners | Python at V1; Node/TS at V2; Go, Java, Ruby at V5 | V1/V2/V5 |

### 5.5 GitHub integration

| Feature | Description | Phase |
|---|---|---|
| GitHub App install | Scoped permissions, per-repo selection, revocable | V1 |
| Installation token rotation | Short-lived tokens minted per operation | V1 |
| Selective content fetch | Contents API + Git Trees API, path-targeted | V1 |
| Webhook ingestion | `push`, `pull_request`, `check_suite`, `installation` | V1 |
| Branch creation | `roottrace/fix-<issue-fingerprint-short>` | V1 |
| Commit via Git Data API | Blob → tree → commit → ref. No working directory, no clone | V1 |
| PR authoring | Structured description: root cause, evidence, confidence, sandbox results | V1 |
| PR status tracking | Open / merged / closed / edited-then-merged | V1 |
| Auto-merge policy engine | Opt-in, per-repo and per-path-glob, with confidence floor | V2 |
| Incremental repo indexing | Re-index only changed files on merge to main | V2 |

### 5.6 Dashboard & analytics

| Feature | Description | Phase |
|---|---|---|
| Overview dashboard | KPI tiles, error volume chart, live investigation feed, health score | V1 |
| Issue list | Grouped signatures with repeat counts, severity, status, trend sparkline | V1 |
| Issue detail | Occurrence timeline, environments, affected endpoints, linked investigations | V1 |
| Investigation detail | Pipeline visual, evidence panel, diff viewer, sandbox console, confidence breakdown | V1 |
| Raw log explorer | Every ingested payload, filterable, searchable, with full JSON inspector | V1 |
| Repeat-error analytics | Top signatures by count, by growth rate, by estimated engineer-hours saved | V1 |
| Pipeline run history | Every run per user, replayable, exportable | V1 |
| Cost & usage panel | Tokens, micro-USD, sandbox minutes per project and per period | V1 |
| API key management | Create, name, scope, reveal-once, rotate, revoke | V1 |
| Settings | Project, repo bindings, model preferences, thresholds, notification prefs | V1 |
| Global command palette | ⌘K navigation and actions | V1 |
| Saved views & filters | Persisted per user | V2 |
| Team members & roles | Owner / maintainer / viewer | V2 |
| Weekly digest email | Signature trends and time saved | V2 |

### 5.7 Platform & account

| Feature | Description | Phase |
|---|---|---|
| Auth | Supabase GoTrue: GitHub OAuth + email magic link | V1 |
| Session management | JWT access + refresh, secure httpOnly cookies, revocation | V1 |
| Multi-tenancy | Postgres RLS on every tenant table | V1 |
| Per-user error history | Every ingested error and investigation retained and queryable | V1 |
| Audit log | Every privileged action recorded, immutable | V1 |
| Rate limiting & quotas | Per key, per project, per plan tier | V1 |
| Data export | Full JSON export of a project's history | V2 |
| Data deletion | Hard delete with cascade, GDPR-shaped | V2 |
| Billing | Stripe, usage-based metering | V6 |

---

## 6. What we are deliberately NOT building

This section exists to prevent scope creep. Each entry is a real temptation that would damage V1.

| Not building | Why |
|---|---|
| **Dark mode** | Explicit product decision. One theme, executed perfectly, beats two done adequately. Light/blue is the brand. |
| **Full repository cloning** | Kills cost, latency, and security posture simultaneously. Selective retrieval is a core differentiator, not a limitation. |
| **A dedicated vector database** | pgvector handles millions of function embeddings comfortably. Adding Pinecone/Weaviate in V1 buys nothing and costs an extra system to operate. |
| **Kubernetes** | V1 traffic fits on managed containers. K8s is weeks of ops work for zero user-visible value at this stage. |
| **Microservices** | Three deployables (api, worker, web) plus a sandbox runner image. Splitting further before load demands it creates distributed debugging problems with no upside. |
| **Auto-merge in V1** | Trust must be earned with a track record. Shipping autonomy before accuracy is proven is how this category of product destroys its own credibility. |
| **Fine-tuning our own model** | We have no proprietary dataset yet. The feedback loop (V3) creates one; fine-tuning is a V6 conversation at the earliest. |
| **Supporting every language at V1** | Python and JS/TS cover the majority of the target market. Each additional language is a full Tree-sitter grammar, dependency toolchain, and sandbox runner. |
| **AI chat** | Genuinely valuable, but it depends on investigations existing and being rich. It is V4 precisely because it is built *on top of* the pipeline. |
| **Mobile app** | Responsive web is sufficient. Nobody reviews a diff on a phone. |
| **Self-hosted / on-prem** | Enterprise ask, enterprise timeline. Design decisions keep it possible (containers, no proprietary cloud services in the hot path) but we do not build it now. |

---

## 7. Competitive positioning

| | Sentry / Datadog | GitHub Copilot | Autonomous agents (Devin-class) | **RootTrace AI** |
|---|---|---|---|---|
| Detects errors | ✅ | ❌ | ❌ | ✅ |
| Finds the code | Stack trace only | You must be in the file | ✅ (clones repo) | ✅ (targeted retrieval) |
| Explains root cause | ❌ | Partial, on request | ✅ | ✅ with bound evidence |
| Writes a patch | ❌ | ✅ (you drive) | ✅ | ✅ (scoped) |
| **Proves the patch** | ❌ | ❌ | Sometimes | ✅ **hard gate** |
| Independent review | ❌ | ❌ | Rare | ✅ separate critic call |
| Cost per incident | Low | Seat-based | High (full-repo context) | Low (narrow retrieval) |
| Trust model | N/A | Human-driven | "Trust the agent" | Evidence + score + human approval |

**Our defensible position:** *proof and narrow retrieval.* Anyone can call a model. Assembling exactly the right context from a stack trace, and then refusing to surface anything that hasn't compiled and passed tests, is the hard part — and it is what makes the output trustworthy enough to act on.

---

## 8. Success metrics

### 8.1 V1 (internal, fake-data)

| Metric | Target |
|---|---|
| Pipeline completion rate on the fixture corpus | ≥ 95% reach a terminal state without operator intervention |
| Root-cause accuracy vs. known ground truth | ≥ 80% exact file+function identification |
| Sandbox validation pass rate on first attempt | ≥ 60% |
| Pass rate after repair loop (≤3 attempts) | ≥ 85% |
| p95 end-to-end latency | ≤ 180 s |
| Mean LLM cost per investigation | ≤ $0.35 |
| Evidence-binding compliance | 100% of surfaced claims cite an artefact |

### 8.2 Post-launch (V2+)

| Metric | Target |
|---|---|
| PR merge rate (merged or edited-then-merged) | ≥ 40% |
| Median time from error to open PR | ≤ 6 min |
| Engineer-hours saved per week per active repo | ≥ 4 |
| False-confidence rate (score ≥0.8 but rejected) | ≤ 5% |
| Weekly active projects retention (8-week) | ≥ 60% |

---

## 9. Risks and mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| AI produces a plausible but wrong fix | **Critical** | Sandbox hard gate + independent critic + confidence floor + human approval default |
| Sandbox escape via AI-generated code | **Critical** | No network, no credentials, read-only rootfs, seccomp/AppArmor, non-root, hard CPU/mem/PID caps, ephemeral. See `11` §6 |
| Prompt injection via error message or source comment | High | Treat all retrieved content as untrusted data; delimiter fencing; instruction-stripping; output schema validation. See `11` §7 |
| LLM cost spiral | High | Per-project cost cap, token budget per stage, cheap models for low-value stages, hard circuit breaker |
| Retrieval misses the real cause | High | Multi-strategy retrieval (frames + graph + vector + history); `insufficient_context` is an explicit, honest terminal state |
| Provider outage or rate limit | Medium | LLM gateway with provider fallback, exponential backoff, request queue |
| Repo has no tests | Medium | AI generates a regression test reproducing the error; validated by failing pre-patch and passing post-patch |
| GitHub API rate limits | Medium | Per-installation token bucket, conditional requests with ETags, aggressive caching of unchanged blobs |
| Secret leakage into prompts or logs | **Critical** | Pre-prompt secret scanner, log redaction filter, KMS-backed secret storage, secrets never in worker env at sandbox runtime |

---

## 10. V1 scope statement (binding)

> **V1 proves the pipeline, not the product.**
>
> V1 runs entirely on the synthetic repository and synthetic error corpus in `fixtures/`. No customer repository is connected. No real credentials exist in the system. The GitHub integration is implemented against a recorded/mocked API surface with a live-mode flag that stays off.
>
> A V1 that runs 14 stages flawlessly on fake data is worth more than a V1 that half-works on real data, because every subsequent phase is built on the assumption that the pipeline is correct.

---

*Next: [`02-SYSTEM-ARCHITECTURE.md`](./02-SYSTEM-ARCHITECTURE.md)*
