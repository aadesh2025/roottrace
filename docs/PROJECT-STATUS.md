# RootTrace AI — Project Status

> **Not a specification.** Every other file in `docs/` is binding; this one is a
> snapshot. It records where the build actually is, what is proved, what is
> open, and what to pick up next. Deliberately un-numbered so it is never
> mistaken for part of the frozen contract set.
>
> **Last updated:** 2026-08-18, this commit (T4.3, Phase 7 in progress).
> Regenerate this from `docs/15-V1-BUILD-PLAN.md` and `git log` — those are the
> authorities. If this file and `15` disagree, `15` wins.

---

## 1. Where the build is, in one line

**Phases 0–6 of 16 are complete, and Phase 7 (retrieval) has three of four
tickets closed.** The system can accept a production error over HTTP,
sanitise it, fingerprint it, group it into an issue, score its severity,
decide whether it deserves a pipeline run, turn it into a structured
`ErrorUnderstanding` with a retrieval plan, resolve every stack frame in the
corpus to the real file it came from, and — for the running example — assemble
frame-direct content, a one-hop call graph, git blame/history, and a
discovered test, reaching `clients/tax_client.py` (a file no frame, breadcrumb,
plan, or path resolver names) through call-graph expansion alone. **Nothing
ranks, deduplicates, or budgets that output yet, and nothing reasons about
it.** No vector search (deferred to V2 by design), no reasoning, no patch, no
sandbox, no dashboard.

**Next:** T4.4 — ranking, the 24,000-token budget, and quality scoring; the
last ticket of Phase 7. `15` §14 still forbids advancing past Phase 7 until
retrieval is genuinely good on the fixture set — T4.4 is where the four bars
`15` §6 states (frame paths, exception family, budget, `insufficient_context`
termination) become checkable together, and where the coordinator's stated
hard-stop condition for this phase gets evaluated.

---

## 2. Phase status

`15` §2 is the canonical order. Note that Phase 5 (fixtures) deliberately runs
*before* Phase 6 (ingestion), reversed from the ticket numbering.

| Phase | Scope | Tickets | Status |
|---|---|---|---|
| 0 | Specification repair | — | ✅ Complete (`18` §9) |
| 1 | Repository / tooling | T1.1 | ✅ Complete |
| 2 | Supabase schema + RLS | T1.2, T1.3 | ✅ Complete |
| 3 | Auth | T1.4 | ⚠️ Complete **except** refresh-token reuse detection — see §5 |
| 4 | FastAPI foundation | T1.5 | ✅ Complete |
| 5 | Fixture system | T3.1–T3.3 | ✅ Complete |
| 6 | Ingestion / fingerprinting | T2.1–T2.5 | ⚠️ Complete **except** T2.1's p95 budget and object storage — see §5 |
| **7** | **Retrieval** | **T4.1–T4.4** | 🔶 **T4.1–T4.3 done; T4.4 next (last ticket of the phase)** |
| 8 | AI reasoning | T5.1–T5.3 | ⬜ Not started |
| 9 | Patch generation | T5.4 | ⬜ Not started |
| 10 | Sandbox validation | T6.1–T6.5 | ⬜ Not started |
| 11 | Repair loop | T7.1 | ⬜ Not started |
| 12 | Independent review | T7.2 | ⬜ Not started |
| 13 | Confidence engine | T7.3 | ⬜ Not started |
| 14 | Fixture GitHub transport | T8.1 | ⬜ Not started |
| 15 | Evaluation harness | T10.1 | ⬜ Not started |
| 16 | Dashboard | T8.2–T8.4, T9.1–T9.8 | ⬜ Not started |

**16 tickets closed of 47.** (39 have their own section in `15`; T9.1–T9.8 are
listed as a table in `15` §11.)

Of the 14 pipeline stages in `03`, **S1–S4 exist in full and S5 exists in
large part** (`receive`, `fingerprint`, `triage`, `understand`, plus S5's
frame path resolution and four of five fetch strategies). What S5 is missing:
strategy C (vector search, deliberately deferred to V2 — the index is never
populated in V1) and T4.4's ranking, dedup, 24k-token budget, and quality
scoring — S5 currently returns raw, unranked, unbudgeted candidates. S4's own
algorithm has an unfilled seam: the LLM structured-extraction step is a
Protocol with one implementation (`UnavailableExtractor`), pending the gateway
at T5.1. See §4.

---

## 3. What is actually built

| Area | State | Proof |
|---|---|---|
| Tooling | `uv` workspace, ruff, mypy `--strict`, pytest, pre-commit, gitleaks, pip-audit, CI on 3 jobs | `make check`, `make audit` |
| Schema | 17 migrations, RLS enabled **and forced** on every tenant table | `tests/integration/test_rls_isolation.py` (82), `test_rls_architecture.py` (10), `test_partitions.py` (9) |
| Auth | `/v1/me`, providers, GoTrue JWT verified against real JWKS | `tests/integration/test_auth_end_to_end.py` |
| API foundation | Request-id middleware, structlog redaction chain, error envelope, container image | `tests/integration/test_container.py`, unit suites |
| Fixtures | 39-module synthetic repo, 25 error payloads + 25 ground-truth files, every bug reproduced by running the code | `make fixtures-verify` (in CI) |
| GitHub client | Full `GitHubGateway` protocol, `FixtureTransport` with **real git object ids**, factory seam enforced by lint-style tests | `apps/worker/tests/test_github_contract.py`, `test_transport_parity.py` |
| Ingest | `POST /v1/events` — auth, rate limit, atomic idempotency claim, per-event validation, sanitisation, batch insert, enqueue | `tests/integration/test_ingest_endpoint.py` (18), `test_ingest_role.py` (16) |
| Fingerprint + issues | Message normalisation, in-app frame extraction, single-statement atomic upsert | `test_issue_upsert.py` (7), incl. 100 concurrent identical inserts |
| Triage | Severity scoring, six gate reasons | `apps/api/tests/test_triage.py` (34) |
| Python SDK | `init`, `capture_exception`, `add_breadcrumb`, ASGI middleware, batching, retry, buffer, never-raises | 171 unit + 13 integration tests |
| S4 `understand` | Deterministic pre-parse, exception taxonomy (10 families), path resolution cascade steps 1–2, retrieval-plan construction, LLM extraction seam with hostile-reply-safe merge | `apps/worker/tests/test_understand_*.py` (161), `tests/integration/test_understand_corpus.py` (182) |
| S5 frame path resolution | Cascade steps 3–4 (suffix match, filename search) against a fetched `RepoTree`, monorepo `root_path`/`service_map` scoping as a hard filter, `test_path_mapping`'s resolution logic as a pure function | `apps/worker/tests/test_retrieve_path_resolution.py` (20), `tests/integration/test_retrieve_path_resolution_corpus.py` (27) — **25/25 corpus frame paths resolve** |
| S5 retrieval strategies A, B, D, E | Frame-direct fetch with `ast`-based function windowing; one-hop call-graph expansion (callees, callers via confirmed `search_symbol` hits, type definitions, import resolution); git blame/recent-commits/release-diff; convention + symbol-grep test discovery. Strategy C stubbed empty (V1 has no code index) | `apps/worker/tests/test_retrieve_ast_index.py` (28), `test_retrieve_import_resolution.py` (10), `test_retrieve_windowing.py` (8), `test_retrieve_strategies.py` (39), `tests/integration/test_retrieve_strategies_corpus.py` (98) — running example retrieves all 4 named files + the introducing commit; 20/23 non-control cases retrieve their own root cause file, the other 3 named and explained (`15` T4.3) |

**Test totals:** 1,773 collected — 878 `unit`, 895 `integration`; 220 tests also
carry the `security` marker. Overall unit coverage **92%** against a ratchet
of **75**; `pipeline/understand` and `pipeline/retrieve` are at 99% each — both
clear `14` §10's ≥95% floor for retrieval/fingerprint/scoring code.

---

## 4. Decisions taken in this session

This session covers T4.1, T4.2, and T4.3 — the first three of Phase 7's four
tickets.

### T4.3 — Retrieval strategies A, B, D, E

- **`ast`, not Tree-sitter, per the T4.1 agreement extended here.** Strategy B
  needs a local symbol table (functions, classes, imports, call sites) for one
  language; the standard library does that natively, and Tree-sitter's
  cross-language payoff only starts to matter at V5. `apps/worker/roottrace_worker/pipeline/retrieve/ast_index.py`.
- **Two pre-existing gaps were found while building this ticket and fixed
  here, not deferred:**
  - Strategies A and B were trusting `understanding.frames[].repo_path` and
    `understanding.failure_point.repo_path` as-is — S4's cascade steps 1–2
    output, never re-verified against the tree. `config-02` exposed it
    immediately: both strategies tried to fetch `services/services/export.py`,
    a well-formed path that isn't a file, and silently returned nothing. Both
    now call T4.2's `resolve_against_tree` first — the wiring T4.2's own note
    said would happen "the first place in the real pipeline," now done.
  - `FixtureTransport.search_symbol` only ever returned *definitions*, which
    was correct for T3.3's original purpose but left strategy B's "callers"
    resolution with nothing to call — `code_edges` is unindexed in V1, so
    "GitHub code search on the symbol name" (`03` §S5) is the *only* V1 path
    to finding a caller, and a caller is a use of a name, not a second
    definition. Broadened to report every occurrence, classified by kind
    (`function`/`class`/`reference`); the two existing contract tests pass
    unchanged since the change is additive.
- **A `"reference"` hit is trusted as a real call only once confirmed by a
  fresh `ast` parse of the candidate file** — a docstring or comment
  mentioning a function's name produces no `ast.Call` node and is silently
  dropped. This is what makes the widened `search_symbol` contract safe: the
  gateway is intentionally as dumb as real GitHub code search, and precision
  is strategy B's job, not the transport's.
- **Import resolution is tree-verified, never guessed**, reusing exactly the
  principle T4.2 established for stack frames: `from services import
  pricing` is genuinely ambiguous (submodule or package symbol?) from the
  statement alone, and only checking the fetched tree resolves it.
  `apps/worker/roottrace_worker/pipeline/retrieve/import_resolution.py`.
- **Three corpus cases have a root cause no strategy here reaches, for three
  distinct structural reasons** — not one bug: `regression-02` is 2 hops from
  its failure point (T4.3 does 1, exactly as `03` §S5 specifies for V1);
  `config-02`'s root cause is the producer of a composition-root-injected
  value, reached by no call edge at all; `type-mismatch-03`'s root cause and
  its failure point are sibling functions connected only through shared
  mutable data, never a call. Named explicitly in
  `tests/integration/test_retrieve_strategies_corpus.py::ROOT_CAUSE_UNREACHABLE_BY_T4_3`,
  with a paired test asserting they *stay* unreachable, so either a fourth
  case joining the set or one of these three starting to resolve is a build
  break in either direction, not a silent drift.
- **`pipeline/retrieve` measures 99% coverage**, clearing `14` §10's ≥95%
  floor for retrieval code specifically (not just the 75% general ratchet) —
  checked deliberately, since retrieval is explicitly named in that floor and
  the general ratchet alone would not have caught falling short of it.

### T4.2 — Frame path resolution

- **A step 1/2 result is trusted only once verified against the tree, never
  before.** `resolve_against_tree` checks every candidate `understand.frames`
  produced — including the 0.95 configured-mapping case — against the fetched
  (and optionally monorepo-scoped) tree before returning it unmodified, and
  falls through to suffix/filename search when it isn't there. `config-02`
  motivated this: heuristic stripping alone produces a well-formed path that
  is not a real file, and nothing before this ticket could tell.
- **Monorepo scoping is a hard filter, applied even to an otherwise-trusted
  step 1/2 result**, not only to steps 3–4. `08` §3.2's own wording —
  "scopes resolution to the right package **before** any matching happens" —
  reads as absolute, and `test_scoping_also_applies_to_a_verified_step_1_2_result`
  asserts it: a configured mapping naming a real file outside the scoped
  package is not returned.
- **Step 4's ambiguous case returns `resolved: null`, never an arbitrary
  pick.** Choosing one of several same-basename candidates would look like a
  resolution and would be a coin flip. `08` §3.2 says "flag
  `low_frame_confidence`"; this is that flag implemented as an honest
  non-answer, consistent with `03` §S5's "we do not guess."
- **The `test_path_mapping` HTTP endpoint (`05` §6.6) is not built.**
  `dry_run_path_mapping` is the endpoint's full resolution logic as a pure
  function, matching the documented response shape exactly — but the route
  needs `repositories` CRUD, which no ticket through Phase 7 builds, and
  T4.2's acceptance criteria (`15` §6) are about the cascade and monorepo
  scoping, not the HTTP surface. Building unrelated CRUD now would be the
  detour `15` §14 warns this phase against. Recorded in `15` T4.2 and `05`
  §6.6, not discovered as a gap later.
- **Result: 25/25 corpus frame paths resolve**, up from T4.1's 24/25 measured
  with S4 alone. `config-02` — the one case T4.1 could not fix, having no
  repo access by design — is now correct via suffix matching.

### T4.1 — S4 `understand`

- **The LLM structured-extraction step is a Protocol, not a call, in V1's
  first cut.** `03` §S4's algorithm has three steps and the middle one needs
  the LLM gateway (T5.1) and prompt system (T5.2), both Phase 8 — after
  retrieval, which `15` §2 and §14 forbid skipping ahead of. `StructuredExtractor`
  is the seam; `UnavailableExtractor` is its only V1 implementation and raises
  immediately, taking the exact fallback `03` §S4 already specifies for LLM
  exhaustion (deterministic pre-parse, `extraction_confidence: 0.5`, continue,
  never terminal) on the first call rather than the third. T5.2 adds a second
  implementation; nothing else in the stage changes. User-approved before
  building — the alternatives considered were pulling T5.1/T5.2 forward
  (rejected: delays retrieval by a full phase) and calling a model directly
  from S4 with no gateway (rejected: writes no `llm_calls` row, violates the
  S4 observability contract).
- **The four-step frame-path cascade (`08` §3.2) splits across two stages.**
  `03` §8.1 already states the boundary — S4 has no repo access and produces
  a plan — and the failure-mode table already sends the unresolved case to
  S5. T4.1 implements steps 1–2 (configured mappings, heuristic prefixes);
  T4.2 owns steps 3–4 (tree suffix match, filename search).
- **The extractor merge is one-directional.** A reply may add to the
  deterministic plan, lower a frame's path confidence, or replace the
  exception family — never remove a file the frames prove was executing,
  raise a confidence the cascade did not earn, invent a frame, or contradict
  runtime metadata. Every dropped claim is recorded, so a degrading
  extractor becomes visible rather than a silently worsening plan. This is
  what makes `03` §S4's "never terminal" fallback safe to build on: a hostile
  or hallucinating extractor can only leave the deterministic floor intact.
- **`classify()` never reads breadcrumbs**, on purpose, even though two
  fixture cases (`race-01`, `resource-01`) are knowable only from them.
  Reading breadcrumbs would fit the classifier to this corpus and improve
  nothing on the next error a customer actually sends (`A1` §9). It costs the
  family-accuracy criterion its margin — 23/25, the bar exactly — and that is
  recorded as a known, intentional gap rather than closed by teaching the
  taxonomy to pattern-match fixture text.
- **`expected.exception_family` and `expected.frame_repo_paths` were added
  to all 25 fixture case files.** Two of T4.1's three acceptance criteria
  (`15` §6) had no ground truth to measure against before this. Both were
  assigned by reading each error, not derived from the resolver being
  measured, and every path was checked against the real synthetic repository.

### Prior session — T1.1–T2.5 (Phases 1–6)

### Architecture

- **`rt_ingest` resolves a contradiction in the spec.** S1 runs in `api`; `api`
  must not hold the service-role key; `raw_events` has forced RLS with no
  INSERT grant to `authenticated`; and ingest carries no user JWT. The
  resolution is a dedicated role with **no `BYPASSRLS` and no password**,
  reached by `SET LOCAL ROLE` and scoped by a `WITH CHECK` against
  `rt.project_id`. The database refuses a cross-tenant write even when the
  handler asks for one. The rejected alternative — granting a write-only role
  read access to every project's membership — is recorded in `15` T2.1.
- **The idempotency claim is atomic** (`SET … NX`), never check-then-act. Two
  concurrent retries of one batch cannot both proceed.
- **The issue upsert is one statement**, not read-then-write. Verified by racing
  100 concurrent identical inserts against real Postgres.
- **The triage gate is advisory; the database is authoritative** (B8). The
  losing side of an insert race attaches to the winner and is indistinguishable
  from having been gated. The DB half is deferred to T8.2.
- **The SDK declares zero runtime dependencies**, so its transport is `urllib`
  and its middleware is raw ASGI. The cost is three things duplicated from
  `apps/api`, and `tests/integration/test_sdk_contract_agreement.py` is the
  single place both packages are imported together so drift fails a test.

### Spec deviations, all with the doc updated in the same commit

- `05` §10 said `import roottrace`; the package is **`roottrace_sdk`**, matching
  the distribution name rather than inheriting the `beautifulsoup4`/`bs4`
  papercut.
- `before_send` receives a **`dict`**, not an object — the attribute form needs
  a model class the zero-dependency rule forbids.
- **Local variables are off by default.** `03` §S1 marks `vars` "redacted", but
  redaction happens at ingest, by which point a password held in a plain local
  has already left the customer's process and matches neither the entropy rule
  nor the pattern list.
- `18` §7 pins the canonical fixture defect to lines 38–43 while `A1` §4's
  comment said line 41. **The registry wins**; the code is written to it.
- `A1` §2's file and test counts were wrong (a required test file had been
  dropped, not the count). Corrected to 42 files, 52 tests, 50 passing.

### Controls that looked applied but did nothing — found and fixed

This is the recurring theme of the session and the thing most worth reading:

| What appeared to work | What was actually happening |
|---|---|
| Security coverage gate | `ls apps/api/roottrace_api/auth*.py` matched a file, not the package — the gate had never run. Now 96%. |
| Container image build | `uv export --no-dev` on the workspace root exports nothing; the image built green with **zero dependencies**. Now asserts `fastapi==` is present and runs an import smoke test. |
| `--no-access-log` | Undone by our own `configure_logging`. Silenced in code instead. |
| Coverage measurement | `source = ["apps","packages"]` — coverage only recurses into directories with `__init__.py`, so it measured only modules some test happened to import. A module with no tests was absent, not 0%. |
| `boundary-01` fixture case | Not a bug at all. With 1-based pagination, `offset - 1` is correct. |
| Both control cases | Leaking raw transport exceptions — they would have been *fixable* cases, not controls. |
| `.gitleaks.toml` | Matched `rt_live_` and not `rt_test_`, though `05` §2.1 defines the format as `rt_{live\|test}`. Half a format reports clean on the half it doesn't read. |
| 500-response handling (T1.5) | FastAPI routes `Exception` to `ServerErrorMiddleware`, which sits *outside* user middleware — so 500s had no `X-Request-ID`, no security headers, and a null `request_id`. |

**Every new guard was verified with a deliberate probe**, not assumed: removing
the redaction processor produces 20 failures; adding a `github_mode` branch
fires 2 of 3 parity rules; reintroducing `RT_COVERAGE_MIN_OVERALL` trips the
namespace guard; disabling the new gitleaks rule fails the new test.

### A correction worth remembering

**I claimed the T2.1 p95 budget was met. It was not.** Corrected in `ee8281b`
with the real numbers. Widening the threshold to make the test green was
explicitly rejected — that deletes the only signal.

---

## 5. Open items, carried forward

| # | Item | Owner ticket | Why it is open |
|---|---|---|---|
| 1 | **Refresh-token reuse detection does not work** | T1.4 | Replaying a consumed refresh token returns 200 at `GOTRUE_SECURITY_REFRESH_TOKEN_REUSE_INTERVAL` of both 10 and 0, suggesting 0 means *unlimited* in this build. A stolen refresh token is replayable; `11` T15's mitigation is incomplete. |
| 2 | **S1 p95 budget not met** | T2.1 | Linux CI: median 28 ms, **p95 226 ms** against a 50 ms target. Bimodal — ~15 samples at 26–33 ms, 5 at 94–230 ms. An unidentified periodic stall, so a real defect rather than platform cost. `xfail(strict=False)` with the numbers in the reason. |
| 3 | **Object storage (S1 step 8) not implemented** | T2.1 → worker | `payload_url` is null. The `api` holds no credential that can write to Supabase Storage, by the same boot invariant that keeps the service-role key out of it. The archive write belongs to the worker. |
| 4 | **Triage is not DB-wired** | T2.4 → T8.2 | The B8 insert-and-handle-conflict path needs the `investigations` table and its partial unique index, which belong to the orchestrator. |
| 5 | **Fixture tree has one revision on disk** | T3.3 | `ref` is resolved, validated and recorded but does not select content. `blame` and `compare` do distinguish revisions. Nothing in V1 reads a historical ref. |
| 6 | **Fixture suite verified against stock `python:3.12-slim`, not the hardened image** | T3.1 → T6.1 | Checked with `--network none`, which is the property T6.1 would otherwise inherit as a surprise. Re-verify at T6.1. |
| 7 | **`replay` and `live` transports raise `TransportUnavailable`** | V2 | Deliberate. They are listed and skipped in the contract suite rather than omitted — an omitted transport is one nobody remembers to add. |
| 8 | **The LLM structured-extraction step of S4 is unimplemented** | T4.1 → T5.2 | `StructuredExtractor` is a Protocol with one implementation, `UnavailableExtractor`, which always raises. S4 runs on the deterministic pre-parse alone until T5.2 adds a real implementation. Deliberate — see `15` T4.1 and §4 above, not a gap discovered late. |
| 9 | **Exception-family accuracy has no margin (23/25, exactly the T4.1 bar)** | T4.1 → T5.2 | `race-01` and `resource-01` are knowable only from breadcrumbs, and the deterministic taxonomy deliberately never reads them (`A1` §9). The extractor at T5.2 is expected to close this; if it does not, the threshold needs revisiting, not the taxonomy. Named explicitly in `tests/integration/test_understand_corpus.py` so a third miss is a build break. |
| 10 | **`POST /v1/repositories/{id}/test_path_mapping` (`05` §6.6) is not wired as an HTTP endpoint** | T4.2 → Phase 16 or first `repositories`-CRUD ticket | `dry_run_path_mapping` is the full resolution logic as a pure function; the route needs `repositories` CRUD, which no ticket through Phase 7 builds. Deliberate scoping decision, not a gap — see `15` T4.2 and `05` §6.6. |
| 11 | **Three corpus cases have no reachable root cause under T4.3's four strategies** | T4.3 → T4.4 / T5.x | `regression-02` (2 hops away), `config-02` (root cause is a composition-root-injected value's producer, no call edge), `type-mismatch-03` (root cause and failure point are sibling functions sharing only data, no call edge). Named by case id in `tests/integration/test_retrieve_strategies_corpus.py::ROOT_CAUSE_UNREACHABLE_BY_T4_3`; whether T4.4's budget allowing a second hop, or S6's reasoning-driven follow-up retrieval, closes any of these is an open question for later phases. |
| 12 | **Release correlation (`03` §S5 strategy D) has no automatic "previous release" lookup** | T4.3 → T8.2 or a `repositories`/releases data source | `strategy_d_git_history`'s `release_diff` is `None` unless the caller supplies `previous_ref` explicitly — the mechanism (`gateway.compare`) is built and tested, but nothing upstream yet knows what the previous release tag was; that needs a releases table or GitHub API call this ticket has no reason to add. |

---

## 6. Known documentation drift

Small, and recorded rather than silently fixed:

- None currently tracked. The two entries previously here — the ADR-LOG count
  in `CLAUDE.md`/`00`, and the 15-vs-17 migration count in `18` §6/`04` §15 —
  were both corrected this session.

---

## 7. Environment notes

- **Local Supabase is currently wedged on Windows.** `supabase start` fails to
  bind port 54322 because Windows' NAT service has reserved the range
  54313–54412. Kong stays "Up" while refusing connections, and the integration
  suite blocks on the first test that reaches it rather than failing. Fix and
  diagnosis are in `A3` §5; it needs `net stop winnat && net start winnat` from
  an **elevated** shell, which briefly drops port forwarding for every container
  on the machine.
- Redis runs on **6380**, not 6379 — binding the default would risk pointing the
  suite at an unrelated project's Redis, which the idempotency tests flush.
- CI is authoritative for the DB-backed integration suite. `test_understand_corpus.py`
  needs no database — it is marked `integration` only to sit with the other
  corpus tests that read the same fixture files, and runs the same in CI or
  locally.

### Commands

```bash
make check              # fmt · lint · typecheck · unit + coverage ratchet
make audit              # gitleaks (full history) · pip-audit · action-pin check
make test-integration   # needs the local Supabase stack + Redis
make test-security      # RLS, tenancy, injection corpus
make fixtures-verify    # ground truth resolved against real code (also in CI)
```

---

## 8. What to continue with

**Phase 7 — retrieval, T4.4 only. T4.1–T4.3 are done.** `15` §6 has the
acceptance criteria; `03` §S4/§S5 has the contracts. **This is the last
ticket of the phase**, and the coordinator's stated hard-stop condition
applies at its end: do not advance into Phase 8 unless the four bars below
are genuinely met, measured, not adjusted to fit.

| Ticket | Scope | Status |
|---|---|---|
| T4.1 | Stage 4 — `understand` | ✅ Done — `apps/worker/roottrace_worker/pipeline/understand/` |
| T4.2 | Frame path resolution | ✅ Done — `apps/worker/roottrace_worker/pipeline/retrieve/path_resolution.py`; corpus at 25/25 |
| T4.3 | Stage 5 — retrieval strategies A, B, D, E | ✅ Done — `apps/worker/roottrace_worker/pipeline/retrieve/strategies.py` |
| T4.4 | Ranking, budget, and quality scoring | ⬜ Next — last ticket of Phase 7 |

**T4.4 turns `RetrievalCandidates` (T4.3's raw, unranked output) into the real
`ContextBundle`** (`03` §S5's output contract): the relevance formula
(`strategy_weight × recency_factor × proximity_factor × symbol_overlap`),
priority-ordered eviction against the 24,000-token hard budget, deduplication
across strategies (T4.3 deliberately does none — `services/checkout.py` in
the running example is currently retrieved once by strategy A, but a case
where strategy B's caller search rediscovers a file strategy A already fetched
is expected and untested until T4.4 merges them), the `quality.score` that
feeds S11's confidence, and the `insufficient_context` termination when
priority 1–4 leaves fewer than 3 files or 800 tokens of `in_app` source. This
needs a real tokenizer — nothing built through T4.3 counts tokens.

**T4.4's own acceptance bar (`15` §6):** budget never exceeded across all 25
cases; priority 1–2 items never evicted; both `unfixable-*` controls terminate
as `insufficient_context` without proceeding to reasoning. Verify these against
the fixture corpus the same way T4.1–T4.3 did — measured, not assumed — before
calling Phase 7 closed.

**The rule that governs this phase**, from `CLAUDE.md` and `15` §14:

> Do not advance past Phase 7 until it is genuinely good on the fixture set.
> Everything downstream inherits its errors, and a confident wrong answer built
> on wrong context passes every later gate.

Two things are already in place to make that measurable, and both should be used
rather than re-derived: the **25-case corpus** with AST-resolved ground truth
(`fixtures/error-corpus/`, `make fixtures-verify`), and the **fixture GitHub
client**, which is the only way retrieval reaches code. P3 binds here — retrieve
narrowly, never wholesale, inside a hard 24,000-token budget with
priority-ordered eviction.

Nothing in the open-items list blocks T4.4. Items 9 and 11 (family accuracy
with no margin; three root causes unreachable by T4.3's strategies) are worth
re-checking once T4.4's budget/eviction exists — a second hop becoming
affordable within budget could close `regression-02` specifically, though not
`config-02` or `type-mismatch-03`, which have no call edge to walk at any hop
count. Neither is T4.4's job to fix; both are worth having in view when
evaluating the phase's hard-stop numbers at the end of this ticket.
