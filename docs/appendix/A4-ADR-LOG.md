# A4 — Architecture Decision Records

> The eight decisions that shape this system, with the reasoning and the trade-offs accepted.

Format: Context → Options → Decision → Consequences → Revisit trigger.

---

## ADR-001 — In-app sandbox for V1 validation; repo CI added in V2

**Status:** Accepted · 2026-08-04
**Supersedes:** the earlier recommendation to rely solely on repo CI

### Context

An AI-generated patch is a hypothesis. The product's entire trust model rests on being able to say *"this was compiled and tested before you were asked to look at it."* That requires executing untrusted code, which is the single largest security surface in the system.

The prior architecture note recommended avoiding a sandbox entirely by pushing to a branch and letting the repo's own CI validate. That reasoning is sound and we have not discarded it.

### Options

| Option | Pros | Cons |
|---|---|---|
| A — Repo CI only | Zero execution infra; uses infrastructure the team already trusts; runs the real full suite | Requires publishing unvalidated code to the customer's repo first; unusable in V1 (no real repos, fake data); excludes repos without CI; minute-scale feedback makes a repair loop impractical |
| B — Own sandbox only | Fast, offline, works with fake data, tight repair loop, works for repos without CI | We own the hardest security problem; never validates against the team's real environment |
| C — **Sandbox now, CI as a second gate in V2** | Every benefit of B immediately, every benefit of A later; the two gates are genuinely complementary | Two validation systems to build and maintain |

### Decision

**Option C.** The sandbox validates before publication. Repo CI is added in V2 as an additional post-publication gate that adjusts confidence.

The determining factor is ordering. CI can only run *after* a PR exists, which means publishing unvalidated code to a customer's repository to discover whether it works — precisely inverting the product's core promise. A pre-publication gate is not optional for a product whose claim is "we don't waste your time with guesses."

### Consequences

- We must build and maintain hardened container isolation (`07`). Eight defence layers, 15 CI-enforced security checks.
- V1 is fully offline-testable, which makes the entire evaluation harness possible.
- The repair loop operates in seconds rather than minutes.
- Repos without CI are fully supported.
- In V2, a patch that passes both gates carries meaningfully stronger evidence than either alone.

### Revisit if

A managed sandboxing service emerges that is materially more secure than our own and does not put a vendor in the critical path.

---

## ADR-002 — Python 3.12 + FastAPI for the backend

**Status:** Accepted

### Context

The backend does three things: serve a high-throughput HTTP API, orchestrate long-running async work, and parse/analyse source code across multiple languages.

### Options

Python/FastAPI · Node/NestJS · Go · Hybrid (Node API + Python workers).

### Decision

**Python throughout.**

The decisive factor is the third workload. Tree-sitter bindings, `libcst`, `ruff`, `bandit`, and `mypy` are all first-class in Python and mediocre or absent elsewhere. A Node backend would shell out to Python for indexing anyway, which means running two languages *and* paying an IPC boundary — strictly worse than just using Python.

The hybrid option was seriously considered and rejected: two deploy pipelines, two dependency sets, and two type systems from day one, for a team that does not yet have the scale to justify that overhead.

Raw CPU throughput is irrelevant here — the system is overwhelmingly I/O-bound on LLM and GitHub calls.

Pydantic v2 is an underrated multiplier: one type definition serves as request validation, response serialisation, LLM output schema, and the JSON Schema injected into prompts. In a system whose central risk is malformed structured output, that single source of truth is worth a great deal.

### Consequences

- Frontend is TypeScript, so we run two languages. Mitigated by generating both TS and Python types from shared JSON Schemas.
- We accept lower raw throughput per instance than Go. Irrelevant at V1 scale, and horizontal scaling is trivial for a stateless API.

---

## ADR-003 — Supabase for database, auth, and storage

**Status:** Accepted

### Context

We need Postgres, vector search, authentication, row-level multi-tenancy, and blob storage, operated by a team with no dedicated SRE.

### Decision

**Supabase** (Postgres 17 + pgvector + GoTrue + Storage + RLS).

The determining factor is **RLS**. Database-enforced tenancy makes an entire class of catastrophic bug structurally impossible. Application-layer tenancy is one forgotten `WHERE project_id = ...` away from a cross-tenant leak, and that mistake is easy to make, easy to miss in review, and devastating when it reaches production.

Auth, pgvector, and storage arriving in the same product removes three integrations we would otherwise build and operate.

**Postgres major version: 17.** Recorded explicitly so the next reader knows it was chosen rather than inherited from a CLI default. Supabase provisions new hosted projects on 17, and the self-hosted default image moved 15 → 17 on 2026-06-17. Pinning local development to 15 would not avoid divergence, it would *create* it — and in the worse direction, because local older than hosted means version differences surface in production rather than on a developer's machine. Three supporting reasons: PG15's extension support was slated to end around May 2026 and is already past, so 15 begins on a deprecation path; every extension we need (`pgcrypto`, `vector`, `pg_trgm`, `btree_gin`) is present on 17, none of them in the set 17 drops (`timescaledb`, `plv8`, `plls`, `plcoffee`, `pgjwt`); and the partition-pruning and vacuum improvements in 16/17 land directly on `raw_events` and `error_occurrences`, the two partitioned tables that carry the highest row volume in the system. Set in `infra/supabase/config.toml`.

### Consequences

- Vendor dependency, deliberately kept shallow: no proprietary SQL, plain-SQL migrations, standard JWT verified by public key. Exit is `pg_dump`/`pg_restore` plus swapping GoTrue for another OIDC provider.
- Workers use `service_role`, which bypasses RLS. Compensated by a repository base class that refuses to build a query without an explicit `project_id`, plus parameterised cross-tenant tests across all 26 tenant tables.
- The RLS model itself proved harder than anticipated and was rewritten during specification review; see `ADR-009`.

### Revisit if

Supabase pricing becomes uncompetitive at scale, or a compliance requirement demands self-hosting.

---

## ADR-004 — Selective file retrieval, never repository cloning

**Status:** Accepted · **Most important architectural decision in the system**

### Context

To diagnose an error we need the relevant source code. The naive approach is to clone the repository.

### Options

| Approach | Data | Cost | Latency | Security surface |
|---|---|---|---|---|
| Full clone | 50 MB – 5 GB | High storage + egress | 10–120 s | Entire codebase at rest on our infrastructure |
| Shallow clone | 5–500 MB | Medium | 3–30 s | Entire working tree at rest |
| **Selective fetch** | **20–200 KB** | **Negligible** | **0.5–3 s** | **Only implicated files, in memory** |

### Decision

**Selective fetch.** Resolve stack frames to repository paths, fetch only those files plus their direct call-graph neighbours, enforce a hard 24,000-token budget.

This is simultaneously the cost strategy, the latency strategy, the accuracy strategy, and the security story. It is not one optimisation among several — it is the architectural choice that makes every other property of the system possible.

**On accuracy specifically:** a smaller, more precisely targeted context produces *better* answers, not worse ones. Models degrade when relevant information is buried in irrelevant information. Retrieving 8,000 well-chosen tokens beats retrieving 200,000 indiscriminate ones on every axis that matters.

### Consequences

- Retrieval quality becomes the system's primary correctness risk, which is why five parallel strategies exist and why retrieval quality feeds directly into confidence.
- Frame-to-path resolution is a hard problem requiring a four-step cascade and a user-facing diagnostic tool.
- We can honestly tell customers we never store their codebase — a materially different security conversation than "we clone your repo."
- Repository indexing (V2) is a *supplement* to selective fetch, not a replacement for it.

### Revisit if

Retrieval quality plateaus below acceptable accuracy despite indexing and improved graph traversal.

---

## ADR-005 — Next.js 14 App Router with Server Components

**Status:** Accepted

### Decision

Next.js 14 App Router, TypeScript, Tailwind, shadcn/ui, Monaco, Recharts.

Server Components let the heavy list views (log explorer at 100k rows, issue list with joined investigation state) render on the server with data already joined — the difference between a snappy dashboard and a loading-spinner product. shadcn/ui gives us accessible primitives whose source we own, which matters because our design system is a specific light/blue aesthetic requiring deep restyling rather than theme tweaks.

### Consequences

- The RSC mental model has a real learning curve; the boundary between server and client components must be deliberate.
- We own the shadcn component source, so we maintain it. Acceptable — it is a small amount of code and full control is exactly what a distinctive design system requires.
- Monaco is heavy; it is dynamically imported and only loaded on the patch tab.

---

## ADR-006 — Light theme only, blue-only brand palette

**Status:** Accepted

### Context

Developer tools default to dark themes. We are choosing differently, and deliberately.

### Decision

**Light theme only. Blue as the sole brand colour. No dark mode in V1.**

Reasoning:

1. **Differentiation.** Every observability tool is dark. A calm, light, precise interface is memorable and signals a different kind of product.
2. **Focus.** One theme executed perfectly beats two done adequately. Dark mode doubles the design surface for every component, every chart, every state.
3. **Colour as signal.** With blue as the only brand colour, any other colour in the interface *means* something. Users learn "coloured = status" and can rely on it. That semantic clarity is impossible in a palette that uses colour decoratively.
4. **Evidence and diffs read better on light.** The core content of this product is source code and diffs, which is what most people read on light backgrounds in their editor anyway.

Status colours (red, amber, restrained green) are permitted **exclusively** for state. Diff green/red is permitted because overriding a universal convention would make the product harder to use in service of a rule meant to make it easier. The sandbox console is deliberately dark because a terminal that isn't dark reads as fake.

### Consequences

- Some users will want dark mode. We will say no in V1 and reconsider only if it becomes a genuine adoption blocker rather than a preference.
- Every component is designed once, thoroughly.
- Charts are distinguished by lightness rather than hue — colour-blind safe by construction and legible in greyscale.

---

## ADR-007 — V1 runs entirely on fake data

**Status:** Accepted

### Context

The natural instinct is to connect a real repository immediately and see it work.

### Decision

**V1 connects zero real repositories.** The full pipeline runs against a synthetic repository and a 25-case error corpus with known ground truth.

### Reasoning

| Reason | Detail |
|---|---|
| Ground truth exists | We can measure accuracy instead of eyeballing plausibility. Without ground truth there is no evaluation harness, and without an evaluation harness every prompt change is a guess |
| Zero risk | A pipeline still being debugged cannot damage a customer's repository |
| Deterministic and offline | Fast, reproducible tests with no external dependency but LLM providers |
| Isolates the real risk | The hard problem is retrieval and validation correctness, not GitHub plumbing |
| Ships faster | No OAuth flow, no App review, no rate-limit debugging in week 2 |

### Consequences

- No customer feedback for ten weeks. Accepted: the pipeline's correctness is not something customer feedback can validate anyway — only ground truth can.
- The GitHub client is fully implemented but runs in `fixture` mode, so V2 flips a config value rather than writing new code.
- Fixtures must be genuinely realistic. A toy repository would let us pass tests a real repository would fail, which would be worse than no test at all.

**The principle:** a V1 that runs 14 stages flawlessly on fake data is worth more than a V1 that half-works on real data, because every subsequent phase assumes the pipeline is correct.

---

## ADR-008 — pgvector inside Postgres; graph as relational tables

**Status:** Accepted

### Context

We need vector similarity search over code embeddings and call-graph traversal.

### Options

Dedicated vector DB (Pinecone/Weaviate) + graph DB (Neo4j) · pgvector + Neo4j · **pgvector + Postgres tables**.

### Decision

**pgvector for vectors, ordinary Postgres tables for the graph.**

**On vectors:** pgvector with an HNSW index handles millions of function-level embeddings with good recall and single-digit-millisecond latency. A dedicated vector database adds a second system to operate, a second consistency model, and a network hop, in exchange for performance we do not yet need. Keeping embeddings in the same transaction as the relational data they describe is worth more than marginal query speed.

**On the graph:** our traversals are 1–2 hops from a known starting node — a bounded index lookup, not a graph algorithm. Postgres does this in milliseconds. Neo4j earns its place when you need unbounded traversal, path-finding, or graph algorithms. Nothing in this design does.

### Consequences

- One database to operate, back up, secure, and monitor.
- If traversal depth requirements grow substantially, we revisit. The `code_edges` schema maps cleanly to a graph DB, so migration is a data move rather than a redesign.

### Revisit if

Vector index exceeds ~50M rows with degrading recall, or a feature requires traversal deeper than 3 hops.

---

## ADR-009 — Tenant authorization via `BYPASSRLS`-owned helpers

**Status:** Accepted · 2026-08-10
**Supersedes:** the `auth_project_ids()` design in the original `04` §12

### Context

The original model applied one generic policy to 22 tables using `project_id in (select auth_project_ids())`. It could not be deployed, for three independent reasons discovered during specification review:

- `projects` has no `project_id` column — the migration fails to compile (**B1**).
- `auth_project_ids()` reads `projects`, whose policy calls `auth_project_ids()` — infinite recursion (**B2**).
- `project_members`, `organizations`, and `organization_members` had **no RLS at all**, so any authenticated user could read every membership row and insert themselves as owner of any project (**B4**).

Underlying all three was a factual error: the belief that `FORCE ROW LEVEL SECURITY` exempts `SECURITY DEFINER` functions. It does not. `FORCE` exempts nothing; only a role holding `BYPASSRLS` does.

### Options

| Option | Assessment |
|---|---|
| A — Drop `FORCE`, rely on owner exemption | Rejected. `FORCE` is what stops the application role bypassing RLS; removing it defeats `ADR-003`'s entire justification for Supabase |
| B — Non-recursive policies only, no helpers | Rejected. "See co-members of my org" is inherently self-referential on `organization_members`; expressing it without a bypassing helper is not possible |
| C — **`SECURITY DEFINER` helpers owned by a `BYPASSRLS` role** | Accepted |

### Decision

A dedicated `rt_auth` schema owned by `rt_rls_owner` — a `NOLOGIN` role created solely to hold `BYPASSRLS`. Five helpers resolve the caller's authorization set. Because RLS is not applied inside them, every policy that calls one terminates in a single hop.

Twenty tables share a generic project-scoped policy; six (`projects`, `organizations`, `organization_members`, `github_installations`, `project_members`, `audit_log`) get bespoke policies because they are keyed on something other than `project_id`.

### Consequences

- A privileged surface now exists, so it is constrained: no helper accepts a user identifier, every one pins `search_path`, `EXECUTE` is revoked from `PUBLIC`, and the owner cannot log in.
- Membership writes are **owner-only** — narrower than data writes — so maintainer self-promotion is an absent capability rather than a policy edge case.
- A cardinality rule (`at least one owner`) cannot be expressed in RLS and needs a trigger.
- Four architecture regression tests guard the design itself, because B2 and B4 arose from plausible-looking code that no functional test would have caught.

### Option B, re-examined empirically during T1.2

Option B was rejected above on reasoning. It was retested against a real database
before writing the migrations, because eliminating `BYPASSRLS` entirely would
remove the only global RLS bypass in our system apart from Supabase's own
`service_role`. The proposal: have `project_ids()` read only the membership
tables, and write the policies on `projects` and the two membership tables inline
so no helper is involved.

**Measured result — the boundary is sharper than "not possible".**

| Variant | Outcome |
|---|---|
| Membership policy with an inline co-member clause referencing its own table | `ERROR: infinite recursion detected in policy for relation` |
| Same clause routed through `projects`, so the recursion is mutual rather than direct | Same error — PostgreSQL detects it across relations |
| Membership policy restricted to **own rows only**, with `projects` and the 20 generic tables inline-reading it | **Works.** Correct isolation, no privileged role |

So `BYPASSRLS` is not required for 21 of the 26 tables. It is required for exactly
one capability: **seeing co-members** on `project_members` and
`organization_members`. Dropping that capability would eliminate the privileged
role outright, at the cost of a user being unable to see who else belongs to their
project or organization.

That is a product decision, not an implementation one, so the design stands.
Recorded here because the trade is now measured rather than assumed, and because
if team management stays out of scope the exchange may be worth making later.

### Known limitation — hosted Supabase

`create role … bypassrls` requires the executing role to hold `BYPASSRLS` itself;
a plain `CREATEROLE` role cannot grant the attribute. Supabase's `postgres` role
holds both locally, and the migration applies cleanly, but this has **not** been
verified against a hosted project. If it fails there, the mitigation is the
own-row-only membership variant measured above, which needs no privileged role at
all.

Three adjacent facts, each found by a migration failing loudly at T1.2 and each
absent from `04` §12.2 as written:

- `postgres` is **not** a superuser on Supabase and must be granted membership in
  `rt_rls_owner` before it can create a schema owned by it, or reassign
  ownership to it.
- `BYPASSRLS` exempts a role from **policies only**. It confers no table
  privileges, so the owner role needs explicit `SELECT` on the three tables its
  helpers read.
- The helpers cannot call `auth.uid()`: they run as `rt_rls_owner`, which has no
  `USAGE` on the `auth` schema, and `postgres` cannot grant it — the attempt
  emits a `WARNING`, not an error, so it appears to work. `rt_auth.uid()` reads
  the request JWT directly instead.

### Revisit if

Supabase changes `service_role`'s RLS semantics, or a future feature needs an authorization predicate that cannot be expressed as a set of project ids, or team-membership visibility leaves scope — see Option B above.

---

## ADR-010 — Composite primary keys on partitioned tables

**Status:** Accepted · 2026-08-10

### Context

`raw_events` and `error_occurrences` declared `id uuid primary key` while being partitioned by a timestamp. PostgreSQL rejects this: a unique constraint on a partitioned table must include every partitioning column. Neither table could be created (**B3**).

### Decision

`primary key (id, received_at)` and `primary key (id, occurred_at)`.

### Consequences

- **No table may declare a foreign key to `raw_events(id)` or `error_occurrences(id)` alone**, because `id` by itself carries no unique constraint. `error_occurrences.raw_event_id` and `investigations.trigger_occurrence_id` are therefore soft references, documented as such, with integrity asserted by test rather than by the database.
- This is a real loss of guarantee, accepted deliberately: partitioning `raw_events` from day one is worth more than referential integrity on a column nothing joins through in the hot path, and retrofitting partitioning onto a 100M-row table under load is the migration this avoids.

---

## ADR-011 — GitHub access behind a transport abstraction

**Status:** Accepted · 2026-08-10

### Context

V1 runs on fixtures and V2 runs against real repositories (`ADR-007`). If fixture-mode logic leaks into pipeline stages, V2 becomes a rewrite of the pipeline rather than a configuration change — and the V1 evaluation stops proving anything about V2 behaviour.

### Decision

A single `GitHubGateway` protocol returning **domain types, never raw GitHub JSON**. Three transports implement it: `fixture`, `replay`, `live`. `RT_GITHUB_MODE` is read in exactly one place — the gateway factory — enforced by a lint rule, because parity maintained by discipline will not survive ten weeks.

Twelve contract tests (GC1–GC12) run against all three transports. A transport is not complete until it passes every one.

### Consequences

- The V1 fixture pipeline exercises the real retrieval logic, real path resolution, and real PR authoring.
- V2 is four configuration values and zero code changes. If V2 requires touching a stage, that is a V1 defect.
- `FixtureTransport` need not imitate GitHub's wire format, only satisfy the contract — which keeps the fixtures maintainable.

---

## ADR-012 — Aggregates exposed only through scoped accessors

**Status:** Accepted · 2026-08-10

### Context

`issue_hourly_counts` and `project_health_daily` are materialised views containing per-project rows. PostgreSQL supports neither RLS on a materialised view nor policy inheritance from source tables, so a direct grant to `authenticated` would be an unrestricted cross-tenant leak of error volumes and health scores (**B6**).

### Decision

`REVOKE ALL … FROM anon, authenticated`. Access only through `SECURITY DEFINER` accessors that intersect with `rt_auth.project_ids()` internally.

### Consequences

- Callers cannot remove the tenant filter; it is inside the function.
- Accessors return **empty rather than erroring** on a foreign project id, so they are not existence oracles.
- Any future materialised view inherits this obligation. A new view granted to `authenticated` is a cross-tenant leak by construction, so the pattern is recorded here rather than left to memory.

---

## Decision summary

| ADR | Decision | Primary driver |
|---|---|---|
| 001 | Own sandbox for V1, CI as second gate in V2 | Cannot publish unvalidated code to prove it works |
| 002 | Python + FastAPI | Multi-language AST tooling is only good in Python |
| 003 | Supabase | RLS makes cross-tenant leaks structurally impossible |
| 004 | Selective retrieval, never cloning | Cost, latency, accuracy, and security in one decision |
| 005 | Next.js App Router + RSC | Server-rendered heavy lists; owned component source |
| 006 | Light theme, blue only | Differentiation; colour reserved to mean status |
| 007 | V1 on fake data | Ground truth is the only way to measure correctness |
| 008 | pgvector + relational graph | One database; our traversals are bounded |
| **009** | **`BYPASSRLS`-owned `rt_auth` helpers** | **Only a `BYPASSRLS` owner breaks the policy recursion** |
| **010** | **Composite PKs on partitioned tables** | **PostgreSQL requires it; partitioning is worth the lost FK** |
| **011** | **GitHub transport abstraction** | **V2 must be config, not a rewrite** |
| **012** | **Scoped accessors for matviews** | **Matviews cannot carry RLS** |

---

## Adding a new ADR

Write one when a decision is **hard to reverse**, **affects multiple components**, or **someone will ask "why is it like this?" in six months.**

Do not write one for reversible implementation choices — library selection for a single module, naming conventions, or anything a single PR could undo.

Never edit a decided ADR. Supersede it with a new one that references it, so the reasoning trail stays intact.

---

*End of documentation set. Return to [`00-README.md`](../00-README.md).*
