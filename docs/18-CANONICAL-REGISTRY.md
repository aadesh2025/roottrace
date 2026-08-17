# 18 — Canonical Registry

> **Single source of truth for every value that appears in more than one document.**
>
> Other documents **reference** this registry. They do not restate its values. Where a document reproduces a value for local readability, it says so explicitly and names this file as authority — and if the two ever disagree, **the owning document named here wins and the copy is the defect.**
>
> Documentation drift is a defect (`00` §7). This file exists to make drift detectable rather than merely discouraged.

---

## 1. How to use this file

| If you are… | Do this |
|---|---|
| Writing a doc that needs a duplicated value | Link here, don't copy. If you must copy for readability, label it "reproduced from `18` §N" |
| Changing a canonical value | Change it in the **owning document**, update this registry, and grep for stale copies — all in the same commit |
| Adding a new duplicated value | Add a row here first, then use it |
| Reviewing a PR | Any changed canonical value must show the owning doc, this registry, and every copy updated together |

---

## 2. Sandbox gates — 9, numbered G0–G8

**Owner:** `07-SANDBOX-VALIDATION.md` §6 · **Summary:** `17` §3

| Gate | Check | Hard? |
|---|---|---|
| G0 | Diff applies cleanly | ✅ |
| G1 | Syntax parses | ✅ |
| G2 | Dependencies resolve offline | ✅ |
| G3 | Compiles / imports | ✅ |
| G4 | **Regression test FAILS on unpatched code** | ✅ |
| G5 | Regression test PASSES on patched code | ✅ |
| G6 | No existing test newly fails | ✅ |
| G7 | No new HIGH static-analysis finding | ✅ |
| G8 | No dangerous construct introduced | ✅ |

**The count is nine.** G0–G8 inclusive. Never "8 gates" — that phrasing counted from G1 and is wrong.

**Never reuse G-numbering for anything else.** Hallucination guardrails are **H1–H13** (§3); sandbox isolation layers are **L1–L8** (§5).

---

## 3. Hallucination guardrails — 13, numbered H1–H13

**Owner:** `06-AI-ENGINE.md` §5

H1 evidence binding · H2 excerpt matching · H3 path existence · H4 symbol existence · H5 diff applicability · H6 scope enforcement · H7 import resolution · H8 compile check · H9 regression-test pre-check · H10 existing-test check · H11 independent critic · H12 confidence gating · H13 human approval.

H7–H10 are *implemented by* sandbox gates G2, G1/G3, G4, and G6 respectively. That cross-reference is why the two numbering systems must stay distinct — a table saying "G7 is implemented by gate G2" is unreadable.

---

## 4. Pipeline stages and timings — 14 stages, S1–S14

**Owner:** `03-PIPELINE-SPEC.md` §6 (timings) and §8 (contracts)

`03` §6 is canonical for **both** the p95 target and the hard timeout of every stage. `02` §9 reproduces the p95 column for architectural context and says so.

**Target p95 ≠ hard timeout.** Exceeding the target means *slow* and burns SLO budget. Exceeding the timeout means *failed*.

| Stage | Target p95 | Hard timeout | Retries |
|---|---|---|---|
| S1 receive | 50 ms | 2 s | 0 |
| S2 fingerprint | 100 ms | 1 s | 3 |
| S3 triage | 200 ms | 1 s | 3 |
| S4 understand | 3 s | 10 s | 2 |
| S5 retrieve | 8 s | 20 s | 2 |
| S6 reason | 25 s | 60 s | 2 |
| S7 patch | 15 s | 45 s | 2 |
| S8 validate | **45 s** | **90 s** | 0 |
| S9 repair | 2 s | 5 s | 1 |
| S10 critique | 12 s | 30 s | 2 |
| S11 score | 200 ms | 1 s | 3 |
| S12 publish | 4 s | 20 s | 3 |
| S13 await_decision | — | 7 d → `stale` | — |
| S14 feedback | 3 s | 10 s | 2 |

| Path | Target p95 | Worst case | Cost |
|---|---|---|---|
| Happy | 115 s | 300 s | $0.32 |
| One repair | 175 s | 460 s | $0.42 |
| Three repairs | 295 s | 780 s | $0.62 |

Pipeline p95 SLO: **240 s** (`12` §8). `RT_PIPELINE_STAGE_TIMEOUT_SECONDS` configures **hard timeouts only**.

> **Open measurement — S8 sandbox p95 (B11).** The 45 s target and 90 s kill are derived from summed per-gate budgets, not observation. Phase 10 (T6.4a) records the real p95/p99 across the fixture corpus and updates this table. Decision rule is fixed in advance: if observed p95 exceeds 70 s, **revisit the 240 s pipeline SLO — do not lower the kill.** Lowering it reintroduces B11 and makes timeouts present as patch-quality failures.
>
> | Measured | Value | Recorded |
> |---|---|---|
> | S8 p95 | *pending T6.4a* | — |
> | S8 p99 | *pending T6.4a* | — |

---

## 5. Sandbox isolation layers — 8, numbered L1–L8; 17 verification checks

**Owner:** `07-SANDBOX-VALIDATION.md` §3 (layers), §12 (checks)

L1 network · L2 filesystem · L3 identity · L4 syscall · L5 resources · L6 time · L7 secrets · L8 lifecycle.

**17 verification checks** in `07` §12, run in CI, blocking deploy. The count rose from 15 during specification repair when B10 added two: the input bundle must survive the `/work` tmpfs mount, and `/work` must be empty at start.

---

## 6. Database — 26 RLS-protected tables

**Owner:** `04-DATA-MODEL.md` §12 (policies), §16 (table register)

| Split | Count |
|---|---|
| Generic project-scoped policy | 20 |
| Bespoke policy | 6 — `projects`, `organizations`, `organization_members`, `github_installations`, `project_members`, `audit_log` |
| **Total RLS-protected** | **26** |

By scope: 3 organization-scoped, 22 project-scoped, 1 dual-scope (`audit_log`).

**Migrations: 17**, listed in `04` §15. Grew from 15 at T2.1, which added `…001600_ingest_role.sql` for the S1 write path (`03` §S1, ADR-009 Option B). `…000900_auth_helpers.sql` must precede `…001000_rls_policies.sql`; `…001500_rls_assertions.sql` runs last among migrations that own a tenant relation — `…001600_ingest_role.sql` grants role privileges only and is exempt from that ordering (`04` §15).

**Partitions are separate relations and inherit nothing.** Each carries its own forced RLS and its own copy of the parent's policies, applied by `rt_admin.secure_partition()` at creation. The 26 count is *logical tables*; the true count of RLS-protected relations is 26 + one per live partition, and grows monthly. See `04` §12.10 (B13).

Authorization helpers live in schema `rt_auth` and are **plain `stable` functions, not `SECURITY DEFINER`**: `uid()`, `org_ids()`, `project_ids()`, `can_write_project(uuid)`, `is_project_admin(uuid)`, `is_org_owner(uuid)`. **Nothing in this system holds `BYPASSRLS`** except Supabase's own `service_role` (ADR-009, Option B).

`uid()` replaces `auth.uid()` throughout, removing a cross-schema dependency `postgres` cannot grant. Membership policies are **own-row-only**, which is what removes the recursion — and therefore the need for a privileged role. Co-member visibility returns with team invites in V2. See `04` §12.2–12.3 and `A4` ADR-009.

---

## 7. Fixtures — 25 cases, 2 of them controls

**Owner:** `14-TESTING.md` §6.2 (schema) · `appendix/A1` (corpus)

**One case, one file:** `fixtures/error-corpus/<case_id>.case.json`. No document restates fixture values.

Canonical values for the reference case `null-prop-01`:

| Field | Value |
|---|---|
| Root cause | `clients/tax_client.py::get_rate`, lines 38–43 |
| Category | `unhandled_error_path` |
| Introduced by | `8a3f1c2e` |
| Error message | `unsupported operand type(s) for +: 'decimal.Decimal' and 'NoneType'` |
| Breadcrumb offset | **T−141 ms** (`09:14:22.340Z` → `09:14:22.481Z`) |
| Confidence | 0.836, band `high` |
| Out-of-scope caller | `services/quote.py::estimate_total` |

**Corpus shape:** 42 files, ~1,780 lines, 52 tests (50 passing, **2 deliberately failing** so gate G6's baseline classification is exercised). Built in T3.1; the earlier "41 files, ~2,400 lines, 49 tests" was an estimate written before the repository existed, and omitted `tests/test_quote.py`, which `A1` §5 requires for `regression-02`.

**Controls:** `unfixable-01`, `unfixable-02` → `insufficient_context`, no patch, no PR, no fabricated root cause. Measured by M14 (2/2) and M15 (0.00), both merge-blocking.

**20 evaluation metrics**, M1–M20, in `14` §6.3.

---

## 8. Roadmap — V1–V6

**Owner:** `16-ROADMAP.md`

| Version | Theme | Ships |
|---|---|---|
| **V1** | Prove the pipeline | 14 stages on fixtures, full dashboard, sandbox, eval harness |
| **V2** | Real repositories | Live GitHub, CI second gate, indexing, Sentry adapter, Node SDK + Node sandbox runner |
| **V3** | Trust and autonomy | Feedback loop, calibration, auto-merge, teams, multi-model consensus, learned retrieval weighting, historical confidence component |
| **V4** | Conversation | AI chat, patch refinement, cross-investigation search |
| **V5** | Scale and languages | Go/Java/Ruby, frontend errors, performance issues |
| **V6** | Platform | Public API, CLI, IDE, custom models, marketplace, billing, fine-tuning |

Two assignments were previously contradictory and are now fixed: **AI chat is V4** (never V3) and the **feedback loop / learned retrieval weighting / historical confidence component is V3** (never V4).

**Implementation phases (0–16)** are separate from roadmap versions and are canonical in `15` §2. All sixteen phases are V1.

---

## 9. Repair log

Issues found during the Phase 0 specification review, with resolution and owning document.

### Blockers

| # | Problem | Resolution | Owner |
|---|---|---|---|
| B1 | `projects` in the generic RLS loop but has no `project_id` | Bespoke policy keyed on `id` | `04` §12.6 |
| B2 | `auth_project_ids()` ↔ `projects` policy recursion | Own-row-only membership policies + inline `projects` read; no privileged role | `04` §12.2–12.3, `ADR-009` |
| B3 | Partitioned tables with PKs excluding the partition key | Composite PKs; soft references documented | `04` §7, `ADR-010` |
| B4 | Membership tables had no RLS — full escalation path | Bespoke policies, owner-only writes, last-owner trigger | `04` §12.4–12.5, `ADR-009` |
| B5 | Nullable `project_id` made org audit events invisible to all | Dual-branch policy + scope check constraint | `04` §12.7 |
| B6 | Matviews cannot carry RLS — cross-tenant aggregate leak | `REVOKE ALL` + scoped `SECURITY DEFINER` accessors | `04` §13.1, `ADR-012` |
| B7 | Ingest idempotency was check-then-act | Atomic `SET NX` claim; `RT-CONFLICT-0004` | `03` §S1 |
| B8 | Concurrent triage could create two investigations | Partial unique index; `UniqueViolation` → attach | `04` §8, `03` §S3 |
| B9 | Cost breaker check-then-act; overshoot scaled with concurrency | Atomic pre-reservation + reconcile on every terminal path | `12` §5.3, `06` §8.2a |
| B10 | `/work` tmpfs mount hid the copied input bundle | Stage at `/opt/roottrace/`; 2 new isolation checks | `07` §7–§8 |
| B11 | 9 gates could not fit a 45 s kill (G6/G7 run twice) | Hard kill **90 s**, p95 target **45 s** | `07` §L6, `03` §S8 |
| B12 | JWT model contradicted itself (JWKS/RS256 vs HS256 secret) | Asymmetric JWKS canonical, algorithm read from the key entry (ES256 in the deployed GoTrue build); `RT_SUPABASE_JWT_SECRET` retired | `A3` §1, `11` §3.1 |
| B13 | Partitions inherit neither RLS nor policies — direct partition query bypassed all tenancy, and the §12.9 assertion would have aborted the first migration | `rt_admin.secure_partition()` at creation; maintenance job creates and secures in one function; second assertion catches enabled-with-no-policy | `04` §12.9–12.10 |

### Contradictions

| # | Problem | Resolution |
|---|---|---|
| C1 | "8 gates" vs G0–G8 | Nine, everywhere (§2) |
| C2 | Guardrails numbered G1–G13, colliding with gates | H1–H13 (§3) |
| C3 | Chat and feedback loop each appeared as both V3 and V4 | Chat V4, feedback V3 (§8) |
| C4 | p95 targets and hard timeouts conflated | Both stated per stage (§4) |
| C5 | Production invariant forced `github_mode=live`, so V1 could not boot | `RT_DEPLOYMENT_TIER` separates rigour from blast radius |
| C6 | Fixture timings and error messages differed across docs | One canonical case file (§7) |
| C7 | Circular `Next:` navigation | Linear chain `00→…→18` |
| C8 | Redundant index on `api_keys.key_hash` | Removed; every retained index now states the query it serves |
| C9 | Replay promised indefinitely, bounded by 90 d retention | `replay_available_until`; `RT-NOTFOUND-0002` |
| C10 | Go/Java/Ruby marked V3 in `07`, V1/V3 in `01`, V5 in `16` | **V5** |
| C11 | Node sandbox runner marked V1 with a Python-only corpus | **V2** |
| C12 | `FORCE ROW LEVEL SECURITY` described as exempting `SECURITY DEFINER` | Corrected — only `BYPASSRLS` exempts |
| C13 | Table counts stale at 22 | **26** (§6) |

---

## 10. Consistency check

Run before any commit that touches a canonical value.

```bash
rg -n "8 gates|8/8 gates"                   docs/   # → 0 hits
rg -n "\bG(9|1[0-3])\b"                     docs/   # → 0 hits (guardrails are H)
rg -n "RT_SUPABASE_JWT_SECRET"              docs/   # → 0 hits (retired)
rg -n "22 tenant|22 tables|25 tables"       docs/   # → 0 hits (26)
rg -n "15 isolation|15/15"                  docs/   # → 0 hits (17)
rg -n "auth_project_ids"                    docs/   # → 0 hits (rt_auth.project_ids)
rg -n "timeout=45|45 s hard|45s hard"       docs/   # → 0 hits (90 s kill)
rg -n "AI chat.*V3|chat.*\(V3\)"            docs/   # → 0 hits (V4)
rg -n "primary key default uuid_generate_v7\(\)" docs/04*  # not on partitioned tables
```

**Owning documents by concern**

| Concern | Owner |
|---|---|
| Gate count and definitions | `07` §6 |
| Guardrail numbering | `06` §5 |
| Pipeline stages and contracts | `03` §8 |
| Stage timings | `03` §6 |
| Database schema, RLS, table register | `04` |
| API contracts and error envelopes | `05` |
| Error code registry | `17` §4 |
| Security control register | `11` §13 |
| Environment variables, flags, invariants | `A3` |
| Make targets | `A3` §5.2 |
| Coverage ratchet | `A3` §6.1 |
| Fixture schema | `14` §6.2 |
| Fixture corpus | `A1` |
| Evaluation metrics | `14` §6.3 |
| GitHub transport contract | `08` §7 |
| Confidence formula | `03` §S11 |
| Roadmap versions | `16` |
| Implementation phases | `15` §2 |
| Architecture decisions | `A4` |

---

*Next: [`appendix/A1-FAKE-DATA-FIXTURES.md`](./appendix/A1-FAKE-DATA-FIXTURES.md)*
