# RootTrace AI — Project Status

> **Not a specification.** Every other file in `docs/` is binding; this one is a
> snapshot. It records where the build actually is, what is proved, what is
> open, and what to pick up next. Deliberately un-numbered so it is never
> mistaken for part of the frozen contract set.
>
> **Last updated:** 2026-08-18, this commit (T4.4 built; Phase 7 HALTED at
> the coordinator's own hard-stop condition — see §1 and §5 item 13). Session
> ran unattended overnight per the coordinator's explicit instructions.
> Regenerate this from `docs/15-V1-BUILD-PLAN.md` and `git log` — those are the
> authorities. If this file and `15` disagree, `15` wins.

---

## 1. Where the build is, in one line — READ THIS FIRST

**All four Phase 7 tickets (T4.1–T4.4) are built, tested, and committed. Phase
7 does *not* clear the coordinator's own stated hard-stop condition, and
nothing has advanced into Phase 8.** Three of T4.4's four acceptance numbers
are clean (budget never exceeded, priority 1–2 never evicted, both controls
terminate correctly); the fourth reveals that `03` §S5's `insufficient_context`
threshold, applied literally to what V1's narrower-by-design retrieval
produces, also fires on **18 of the 23 non-control corpus cases** — cases
whose own ground truth (`expected.final_status: "awaiting_decision"`) says
they should reach reasoning. **No threshold was adjusted and no retrieval
logic was tuned to make the corpus pass** — per explicit instruction, this was
stopped and written up instead. Full detail in §5 item 13; this is the thing
to read before anything else in this file.

The system, as built: accept a production error over HTTP, sanitise it,
fingerprint it, group it into an issue, score its severity, decide whether it
deserves a pipeline run, turn it into a structured `ErrorUnderstanding` with a
retrieval plan, resolve every stack frame in the corpus to the real file it
came from, assemble frame-direct content plus a one-hop call graph plus git
history plus a discovered test — reaching `clients/tax_client.py` for the
running example (a file no frame, breadcrumb, plan, or path resolver names)
through call-graph expansion alone — and rank/dedupe/budget all of that into a
real `ContextBundle`, or honestly terminate as `insufficient_context` when it
mechanically should. No vector search (deferred to V2 by design), no
reasoning, no patch, no sandbox, no dashboard.

**Next, once the calibration question in §5 item 13 is decided:** either
adjust `03` §S5's threshold (and record why, in the same commit), extend the
call-graph to 2 hops when budget allows (would help `regression-02`
specifically, not the composition-root or shared-data cases), or accept that
V1's narrower retrieval genuinely needs a broader `insufficient_context`
trigger reserved for cases that look like the two designed controls, not
every case with a self-contained one-file bug. This is a design decision, not
an implementation task — resolve it before writing any more code against it.

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
| **7** | **Retrieval** | **T4.1–T4.4** | 🛑 **All 4 tickets built; phase HALTED at its own hard-stop — see §5 item 13** |
| 8 | AI reasoning | T5.1–T5.3 | ⬜ Not started |
| 9 | Patch generation | T5.4 | ⬜ Not started |
| 10 | Sandbox validation | T6.1–T6.5 | ⬜ Not started |
| 11 | Repair loop | T7.1 | ⬜ Not started |
| 12 | Independent review | T7.2 | ⬜ Not started |
| 13 | Confidence engine | T7.3 | ⬜ Not started |
| 14 | Fixture GitHub transport | T8.1 | ⬜ Not started |
| 15 | Evaluation harness | T10.1 | ⬜ Not started |
| 16 | Dashboard | T8.2–T8.4, T9.1–T9.8 | ⬜ Not started |

**17 tickets closed of 47** (T4.4 is the 17th) **, but "closed" here means
"built to its own literal acceptance criteria," not "cleared Phase 7."** See
§1. (39 tickets have their own section in `15`; T9.1–T9.8 are listed as a
table in `15` §11.)

Of the 14 pipeline stages in `03`, **S1–S5 all exist**, S5 in full: frame
path resolution, four of five fetch strategies (strategy C deliberately
deferred to V2 — the index is never populated in V1), and ranking/dedup/budget/
quality scoring. S4's own algorithm has an unfilled seam: the LLM
structured-extraction step is a Protocol with one implementation
(`UnavailableExtractor`), pending the gateway at T5.1. See §4.

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
| S5 ranking, budget, quality scoring | `(priority, -relevance)` admission against the 24k token budget (dependency-free, deliberately-overcounting estimate); literal `03` §S5 relevance formula; dedup across strategies; `quality.score` (T4.4's own weighted formula, `03`/`06` §S11 consumes it opaquely); `ContextBundle` / `InsufficientContext` as the real, Pydantic S5 output contract | `apps/worker/tests/test_retrieve_tokens.py` (4), `test_retrieve_quality.py` (8), `test_retrieve_ranking.py` (25), `tests/integration/test_retrieve_ranking_corpus.py` (75) — **budget never exceeded (0/25), priority 1–2 never evicted, both controls terminate correctly; 18/23 non-control cases also terminate as `insufficient_context`, against ground truth — see §5 item 13** |

**Test totals:** 1,885 collected — 915 `unit`, 970 `integration`; 220 tests also
carry the `security` marker. Overall unit coverage **92%** against a ratchet
of **75**; `pipeline/understand` and `pipeline/retrieve` are at 99% each — both
clear `14` §10's ≥95% floor for retrieval/fingerprint/scoring code.

---

## 4. Decisions taken in this session

This session covers all four Phase 7 tickets, T4.1 through T4.4.

### T4.4 — Ranking, budget, quality scoring — and the finding that stopped the phase

**Read this section in full before doing anything else with Phase 7.**

- **The mechanism is a correct, literal implementation of `03` §S5.** The
  relevance formula (`strategy_weight × recency_factor × proximity_factor ×
  (1 + 0.15 × symbol_overlap)`), the nine-tier eviction priority, the
  24,000-token hard budget, and the `insufficient_context` threshold ("fewer
  than 3 distinct priority 1–4 files or fewer than 800 in-app tokens") are all
  built as specified, in `apps/worker/roottrace_worker/pipeline/retrieve/
  {ranking,quality,tokens,bundle}.py`, with 99% coverage on the package
  (98–100% per file) and 75 corpus-level tests plus 37 unit tests.
- **No tokenizer dependency**, the same class of decision as `ast` over
  Tree-sitter (T4.1–T4.3), extended: `06` §2.2 routes every reasoning tier
  across two providers (Anthropic primary, OpenAI failover, deliberately
  reversed for the critic), and no single tokenizer is exact for both —
  Anthropic ships none offline at all. `chars / 3.5`, rounded up, deliberately
  overcounts rather than aims for average accuracy, since undercounting
  against a hard ceiling is the unrecoverable failure mode.
- **Two real bugs were found and fixed while measuring the corpus, both in
  symbol matching, not in the ranking algorithm itself:**
  - `symbols_defined` (from `ast_index`) carries qualified names
    (`"CheckoutService.calculate_total"`); `implicated_symbols`/
    `should_fetch_by_symbol` (from S4) carry bare names
    (`"calculate_total"`). A naive equality check between them — which is
    what the first version of this code did — silently zeroed the
    symbol-overlap relevance bonus and mis-flagged every resolved method as
    an "unresolved symbol" gap on any class-based codebase, which this one
    is throughout. Fixed with `_matches_symbol` (exact match or
    `qualname.endswith(f".{bare_name}")`), covered by
    `test_a_qualified_method_name_matches_its_bare_implicated_symbol` as a
    named regression test.
  - **Recency was silently a no-op.** `RetrievedFile.blame` is never
    populated by any T4.3 strategy (blame is attached to the assembled
    `BundleFile` only for the admitted failure-point entry, downstream of
    ranking) — so the first version of `_rank_files` read `item.blame`,
    which was always `None`, making `recency_factor` always `1.0` regardless
    of how old the introducing commit actually was. Fixed by computing the
    failure point's blame date *before* ranking and threading it in via
    `commit_date_by_path`, so `_relevance` sees the real date.
- **The finding that halted the phase.** Measuring T4.4's own acceptance
  criteria against the full 25-case corpus:

  | Bar (`15` §6) | Result |
  |---|---|
  | Budget never exceeded across all 25 cases | ✅ 0/25 over budget |
  | Priority 1–2 items never evicted | ✅ verified directly, every case that reaches ranking |
  | Both controls terminate as `insufficient_context` | ✅ 2/2 (`unfixable-01`, `unfixable-02`) |
  | *(implicit: everything else proceeds to reasoning)* | ❌ **18 of 23 non-control cases also terminate as `insufficient_context`** |

  Every one of those 18 carries `expected.final_status: "awaiting_decision"`
  (`14` §6.2) — the corpus's own ground truth says they should reach S6, not
  abstain. Hand-checked several by reading the actual source
  (`key-error-01`'s `verify_signature`, `config-01`'s `region_config`):
  retrieval is not missing anything real. These are single, self-contained
  functions that call nothing but stdlib and reference no type worth
  expanding — the bug *is* the whole function, correctly and completely
  retrieved, and priority 1–4 mechanically tops out at one file because there
  is genuinely only one file's worth of directly-implicated code. `03` §S5's
  threshold, read literally, appears calibrated for a retrieval richer than
  what V1's deliberately narrow scope (P3: retrieve narrowly; strategy B at 1
  hop; strategy C deferred to V2) is capable of producing for this whole
  shape of bug.
  - Exact case list, and the corpus test that keeps it honest rather than
    silently absorbing drift in either direction:
    `tests/integration/test_retrieve_ranking_corpus.py::INSUFFICIENT_CONTEXT_ON_FIXABLE_CASES`.
- **Explicitly not fixed by adjusting the threshold, extending the hop count,
  or any other change to make the corpus pass.** Per direct instruction: "do
  not adjust the threshold, do not tune the code to fit this corpus
  specifically, do not move on anyway." This is a design decision about what
  `insufficient_context` is *for* in a narrower-than-spec-assumed V1 — not an
  implementation bug this session is positioned to resolve unilaterally.
- **Everything that could be verified honestly was.** Coverage floor
  (`14` §10, ≥95% for retrieval) checked deliberately for `pipeline/retrieve`
  specifically, not inferred from the general 75% ratchet — same discipline
  as every phase before this one.

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

**Item 13 is the one that matters this morning — it is the reason Phase 7 is
halted. Everything else in this table is routine carry-forward, same as any
other session.**

| # | Item | Owner ticket | Why it is open |
|---|---|---|---|
| 13 | 🛑 **`03` §S5's `insufficient_context` threshold, applied literally, wrongly terminates 18/23 fixable corpus cases** | T4.4 → a design decision, not an implementation ticket | See §4 T4.4 above for the full write-up. Budget/priority-eviction/control-termination all measure clean; the fourth T4.4 acceptance number does not. `key-error-01`, `config-01`, and others checked by hand: retrieval is complete and correct for these — a single self-contained function with no callees or type references mechanically produces exactly one priority-1–4 file, and "≥3 distinct files" is definitionally unreachable for that shape of bug. Not adjusted, not tuned around. **Blocks any further Phase 8 work until decided.** Exact case list: `tests/integration/test_retrieve_ranking_corpus.py::INSUFFICIENT_CONTEXT_ON_FIXABLE_CASES`. |
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

**Phase 7 is built (all four tickets) and HALTED at its own hard-stop
condition. There is nothing to "continue" inside Phase 7 until item 13 (§5)
is decided — the retrieval mechanism itself is not what needs work.**

| Ticket | Scope | Status |
|---|---|---|
| T4.1 | Stage 4 — `understand` | ✅ Done — `apps/worker/roottrace_worker/pipeline/understand/` |
| T4.2 | Frame path resolution | ✅ Done — `apps/worker/roottrace_worker/pipeline/retrieve/path_resolution.py`; corpus at 25/25 |
| T4.3 | Stage 5 — retrieval strategies A, B, D, E | ✅ Done — `apps/worker/roottrace_worker/pipeline/retrieve/strategies.py` |
| T4.4 | Ranking, budget, and quality scoring | ✅ Built, tested, 3/4 acceptance numbers clean — see §5 item 13 for the 4th |

**The one decision that unblocks everything else** (§5 item 13, §4's T4.4
section has the full evidence): `03` §S5's `insufficient_context` threshold —
"fewer than 3 distinct priority 1–4 files or fewer than 800 in-app tokens" —
fires correctly on the 2 designed controls, but *also* fires on 18 of 23
fixable cases whose own ground truth expects them to reach reasoning. This is
not a retrieval gap (checked by hand against several cases; the code finds
everything real there is to find) and not something this session judged
itself entitled to resolve alone, per explicit standing instruction. Plausible
directions, none chosen:

1. **Loosen the threshold for V1** specifically, on the reasoning that it was
   calibrated for a retrieval richer than P3's "narrow, 1-hop, no vector
   search" scope actually produces — and record *why* in `03` §S5 in the same
   commit, since the spec is supposed to be binding, not silently
   reinterpreted.
2. **Extend strategy B to 2 hops** when the (now-existing) token budget has
   room — `03` §S5 already allows this ("2 hops only if budget remains").
   Would close `regression-02` specifically (its root cause is exactly 2 hops
   away) but not `config-02` or `type-mismatch-03`, which have no call edge to
   walk at any hop count, and would still leave most of the 18 unaddressed.
3. **Accept the threshold as-is and treat this as correct, intentional
   behaviour** — i.e., decide that V1's retrieval genuinely should abstain on
   any bug this self-contained, and that the corpus's `expected.final_status`
   for these 18 cases needs revisiting instead of the code. This would be a
   significant, visible change to what the corpus is asserting and should not
   be decided without the person who designed the corpus.

Whichever direction, the fix belongs in the threshold/corpus layer, not by
adding retrieval strategies that only exist to manufacture a third file.

**Once decided:** re-run `tests/integration/test_retrieve_ranking_corpus.py`
(update `INSUFFICIENT_CONTEXT_ON_FIXABLE_CASES` to match whatever the new,
correct behaviour is) and confirm all four `15` §6 T4.4 bars pass together,
non-controls proceeding to reasoning included, before treating Phase 7 as
cleared. Only then does `15` §14's rule apply as a green light rather than a
red one:

> Do not advance past Phase 7 until it is genuinely good on the fixture set.
> Everything downstream inherits its errors, and a confident wrong answer
> built on wrong context passes every later gate.

**What is not blocked:** everything else built this session (T4.1–T4.4's
mechanisms, the fixed symbol-matching and recency bugs, the corpus tooling)
is real, tested, and committed regardless of how item 13 resolves — none of
it needs to be redone, only the threshold/corpus question needs a decision
before Phase 8 (reasoning, T5.1) can begin.
