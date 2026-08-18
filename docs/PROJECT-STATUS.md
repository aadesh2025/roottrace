# RootTrace AI — Project Status

> **Not a specification.** Every other file in `docs/` is binding; this one is a
> snapshot. It records where the build actually is, what is proved, what is
> open, and what to pick up next. Deliberately un-numbered so it is never
> mistaken for part of the frozen contract set.
>
> **Last updated:** 2026-08-18, this commit (T5.2 — the prompt system —
> built; T5.1's LLM gateway built earlier the same day; Phase 8 underway.
> T4.4's calibration finding resolved by the coordinator before either;
> Phase 7 cleared first — see §1 and §5 item 13). Session ran unattended
> overnight per the coordinator's explicit instructions, halted at the
> hard-stop as instructed, resumed with the coordinator's decision, then
> continued into Phase 8 same-day.
> Regenerate this from `docs/15-V1-BUILD-PLAN.md` and `git log` — those are the
> authorities. If this file and `15` disagree, `15` wins.

---

## 1. Where the build is, in one line — READ THIS FIRST

**Phase 7 (retrieval) is complete and cleared. Phase 8 is underway: T5.1
(LLM gateway) and T5.2 (prompt system) are both built, tested, and
committed; T5.3 (Stage 6 `reason`) is next.**

Phase 7's hard-stop condition (§5 item 13) was resolved by the coordinator
before Phase 8 started: `03` §S5's original `insufficient_context` threshold
("fewer than 3 distinct files or fewer than 800 tokens") could not
distinguish a real, thin, self-contained bug from a designed control by
evidence volume — `external-03` (real) and `unfixable-01` (control) admit
the *identical* 2 files / 1231 tokens. The threshold was lowered to what S5
can honestly judge (did retrieval find real content, full stop); judging
*fixability* moved to S6, not yet built. Full detail in §5 item 13.

**T5.1, the LLM gateway (`apps/worker/roottrace_worker/ai/*.py`):** one
seam every pipeline stage will call through — provider selection and
failover (Anthropic/OpenAI, real SDKs behind a `Provider` seam matching
`GitHubGateway`'s shape), retry with backoff, the three-attempt structured-
output ladder, exact token/cost accounting, the B9 cost-cap circuit breaker,
outbound secret redaction, deterministic caching, and `llm_calls`
persistence via a real `TenantRepository` (`11` §4 Layer 3, implemented for
the first time). All three of `15` T5.1's accept criteria hold, tested
against a scriptable `FakeProvider`. Two responsibilities this ticket's
own table names were deliberately not built yet (a provider-health circuit
breaker; provider-side prompt caching) — disclosed in §5 items 16–17, not
silently skipped.

**T5.2, the prompt system (`apps/worker/roottrace_worker/ai/prompts/*`):**
five-layer assembly, `<untrusted_context>` fencing with tag neutralisation,
instruction-pattern flagging, every prompt `A2` gives literal text for
shipped as versioned `.md` files, and a real `GatewayExtractor` closing the
`StructuredExtractor` seam T4.1 left open — `understand` is now the first
stage that can genuinely call a model, tested end-to-end against
`FakeProvider`. Building this **surfaced two real bugs already living in
T5.1's gateway** (`suspicious_content_detected` always persisted `False`
regardless of the actual prompt; the output-side echo check rejected
immediately instead of retrying once, contradicting `06` §3.2) — both
found only because this ticket is what first makes
`flagged_injection_patterns` genuinely non-empty, both fixed in the same
commit. A third drift, also fixed: T5.1's schema-repair prompt was a
hardcoded paraphrase, not `A2` §9's literal, binding text. 99% coverage on
`ai/` + `pipeline/understand` combined. Full detail in §4's T5.1/T5.2
sections.

The system, as built: accept a production error over HTTP, sanitise it,
fingerprint it, group it into an issue, score its severity, decide whether it
deserves a pipeline run, turn it into a structured `ErrorUnderstanding` with a
retrieval plan, resolve every stack frame in the corpus to the real file it
came from, assemble frame-direct content plus a one-hop call graph plus git
history plus a discovered test, rank/dedupe/budget all of that into a real
`ContextBundle` for all 25 corpus cases — and can now turn any of that into a
real, structured, cost-accounted, failover-safe LLM call: S4's `understand`
stage can genuinely call a model end-to-end (`GatewayExtractor`, tested
against `FakeProvider`), though nothing constructs one for a live
investigation yet (no orchestration ticket exists). S6 (`reason`) already
has its prompt text shipped (`reason/v3.md`, T5.2) but no output contract
and no calling code. No vector search (deferred to V2 by design), no
reasoning, no patch, no sandbox, no dashboard yet.

**Next:** T5.3 (Stage 6 `reason`) — per `15` §7. See §8.

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
| **7** | **Retrieval** | **T4.1–T4.4** | ✅ **Complete — all 4 tickets built, calibration finding resolved, see §5 item 13** |
| 8 | AI reasoning | T5.1–T5.3 | 🔶 In progress — T5.1/T5.2 done, T5.3 not started |
| 9 | Patch generation | T5.4 | ⬜ Not started |
| 10 | Sandbox validation | T6.1–T6.5 | ⬜ Not started |
| 11 | Repair loop | T7.1 | ⬜ Not started |
| 12 | Independent review | T7.2 | ⬜ Not started |
| 13 | Confidence engine | T7.3 | ⬜ Not started |
| 14 | Fixture GitHub transport | T8.1 | ⬜ Not started |
| 15 | Evaluation harness | T10.1 | ⬜ Not started |
| 16 | Dashboard | T8.2–T8.4, T9.1–T9.8 | ⬜ Not started |

**19 tickets closed of 47** (T5.2 is the 19th), **Phase 7 cleared, Phase 8
underway.** See §1. (39 tickets have their own section in `15`; T9.1–T9.8
are listed as a table in `15` §11.)

Of the 14 pipeline stages in `03`, **S1–S5 all exist**, S5 in full: frame
path resolution, four of five fetch strategies (strategy C deliberately
deferred to V2 — the index is never populated in V1), and ranking/dedup/budget/
quality scoring. **S4's seam is closed** — `GatewayExtractor` (T5.2) is a
real `StructuredExtractor` implementation, assembling the five-layer prompt
(`ai/prompts`, T5.2) and calling `LLMGateway.complete` (T5.1), tested
end-to-end against `FakeProvider`. What is still missing for S4 to run
against a real investigation is orchestration — nothing yet constructs a
`GatewayExtractor` with real IDs and calls `understand(event,
extractor=...)` for a live error. S6 (`reason`, T5.3) has no prompt
integration, no output contract, and no code yet. See §4.

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
| S5 ranking, budget, quality scoring | `(priority, -relevance)` admission against the 24k token budget (dependency-free, deliberately-overcounting estimate); literal `03` §S5 relevance formula; dedup across strategies; `quality.score` (T4.4's own weighted formula, `03`/`06` §S11 consumes it opaquely); `ContextBundle` / `InsufficientContext` as the real, Pydantic S5 output contract; `insufficient_context` threshold revised (§5 item 13) to judge "did retrieval find real content," not fixability | `apps/worker/tests/test_retrieve_tokens.py` (4), `test_retrieve_quality.py` (8), `test_retrieve_ranking.py` (25), `tests/integration/test_retrieve_ranking_corpus.py` (77) — **budget never exceeded (0/25), priority 1–2 never evicted, all 25 corpus cases (including both controls) correctly reach a `ContextBundle` — see §5 item 13 for the fixability question this defers to S6** |
| LLM gateway (T5.1) | `LLMGateway.complete` — provider seam (`Provider` Protocol, real Anthropic/OpenAI SDKs, `FakeProvider` for tests) with tier failover and per-provider retry/backoff; the three-attempt structured-output ladder; exact token/cost accounting from provider-reported usage; the B9 cost-cap circuit breaker (atomic Redis reserve/reconcile); outbound secret redaction; deterministic caching; `llm_calls` persistence via a real `TenantRepository` | `apps/worker/tests/test_ai_*.py`, `tests/integration/test_ai_db_persistence.py` (3, real Postgres), `tests/integration/test_ai_redaction_contract_agreement.py` (2, drift check against `apps/api`'s ingest sanitiser) — **all three `15` T5.1 accept criteria hold** |
| Prompt system (T5.2) | Five-layer assembly (`ai/prompts/assembly.py`); `<untrusted_context>` fencing with tag neutralisation and instruction-pattern flagging; every `A2`-literal prompt shipped as a versioned `.md` file, loaded through `PromptRegistry`; `GatewayExtractor` — the real `StructuredExtractor`, closing T4.1's seam, tested end-to-end through `understand(...)` against `FakeProvider` | `apps/worker/tests/test_ai_prompts_*.py`, `test_understand_gateway_extractor.py`, `test_understand_extraction_schema.py` — **both `15` T5.2 accept criteria hold; found and fixed two real T5.1 gateway bugs in the process (§4)** |

**Test totals:** 2,037 collected — 1,060 `unit`, 977 `integration`; 220 tests
also carry the `security` marker. Overall unit coverage **93%** against a ratchet
of **75**; `pipeline/understand`, `pipeline/retrieve`, and `ai/` are all at
98–99% — clearing `14` §10's ≥90%/≥85% pipeline-stage floor (the nearest
named category — `14` has no dedicated "AI engine" row).

---

## 4. Decisions taken in this session

This session covers all four Phase 7 tickets (T4.1–T4.4) and Phase 8's
first two tickets, T5.1 and T5.2.

### T5.2 — The prompt system

**Read `06` §3.3's implementation note alongside this section.**

- **Every `A2`-literal prompt shipped as a file, not just `understand`'s.**
  `A2` §1 states plainly that these "ship as files"; nothing in `03`/`06`/`15`
  scopes that to only the stages with a live caller today. `reason/v3.md`,
  `patch/v4.md`, `critique/v2.md`, `repair/v1.md`, `pr_description/v2.md`,
  and `schema_repair/v1.md` all exist now, verbatim from `A2`, even though
  only `schema_repair` has a caller (`gateway.py`'s repair path) and only
  `understand` has a real stage behind it. `repair/v1.md`'s gate-specific
  instruction table (`A2` §7) is stored as a small data module
  (`prompts/repair/gate_instructions.py`) for the same reason — data `A2`
  gives literally, with no caller yet (the repair loop is T9.1, Phase 11).
- **The shared L1 layer is versioned too**, as `prompts/system/v1.md` —
  `A2` §10's own registry table doesn't list it (only per-stage task
  layers), but there is no reason the one prompt fragment every single
  call includes should be exempt from the same versioning discipline as
  every other layer. Added to `registry.yaml`'s `current` mapping
  alongside the stages `A2` §10 does name.
- **`assemble_prompt` produces exactly the `system`/`user` split `T5.1`
  already committed `RenderedPrompt` to** — L1–L3 concatenated into
  `system`, L4 (fenced, tag-neutralised, flagged) + L5 (schema + worked
  example) into `user`. Built this way specifically so T5.1's contract
  didn't need to change to accommodate T5.2; the two tickets' interfaces
  were designed to fit before either was finished.
- **`GatewayExtractor` closes the `StructuredExtractor` seam T4.1 opened
  and T5.1 could not fill alone.** L2 (the domain layer) renders
  `taxonomy.PROFILES` — the same table T4.1 already used for deterministic
  classification, now shown to the model too, since `A2` §3 calls for it
  verbatim. `A2` §3's "filtered to the detected language" is simplified to
  "the whole table, unfiltered" for V1 — the corpus and synthetic repo are
  Python-only throughout, and `PROFILES` carries no per-language variants
  to filter *between*; revisit when a second language's idioms exist.
  `ExtractionRequest`'s fields (exception message, pre-parsed frames,
  breadcrumbs, request record) are fenced as L4 — all customer-controlled
  at runtime, so all untrusted by the same rule that governs retrieved
  source. Tested end-to-end through `understand(...)` with `FakeProvider`,
  not just unit-tested in isolation — this is the first place retrieval-
  adjacent code, the prompt system, and the gateway actually compose.
  **Still not wired to a live investigation**: no orchestration ticket
  constructs a `GatewayExtractor` with real IDs and calls `understand(event,
  extractor=...)` for an actual error yet (§5 item 15's factory gap, plus
  the orchestration layer itself, neither built by any ticket so far).
- **Two real bugs in T5.1's own gateway, found only because this ticket
  is what first makes `flagged_injection_patterns` genuinely non-empty** —
  T5.1's own tests always constructed one by hand, so these two paths
  executed but were never exercised meaningfully:
  - `suspicious_content_detected` was hardcoded `False` on every persisted
    `llm_calls` row in `_record_call`, regardless of the prompt's actual
    flags — only the `LLMResult` returned to the caller carried the real
    value. This directly contradicted T5.2's own accept criterion
    ("injection phrases are ... recorded on the `llm_calls` row"): the row
    is what "recorded" means. Fixed by threading the flag through
    `_record_call`'s signature.
  - The output-side echo check (`06` §3.2: "the response is rejected **and
    retried once**") instead raised `SuspiciousContentRejectedError`
    immediately, with zero retries. Fixed: on a flagged echo, the gateway
    re-dispatches the exact prompt that produced the flagged response to
    the same tier once more — a real, billed, `llm_calls`-recorded call —
    accepting the retry if it parses and comes back clean, raising only if
    the retry also echoes a flagged pattern or fails to parse.
  - Named in `tests/test_ai_gateway.py`:
    `test_a_response_echoing_a_flagged_pattern_is_retried_once_then_rejected`,
    `test_a_response_echoing_a_flagged_pattern_that_clears_on_retry_succeeds`,
    `test_every_suspicious_content_check_writes_a_flagged_llm_calls_row`.
- **A third drift, also fixed here:** T5.1's `gateway.py` hardcoded its own
  schema-repair instruction — a paraphrase, not `A2` §9's literal
  `schema_repair/v1.md` text — because the prompt registry it should have
  read from didn't exist yet. `structured.build_repair_prompt`'s signature
  changed to accept the template and system text as parameters, loaded by
  the caller from the registry, rather than owning a second copy of either.
- **`pipeline/understand` + `ai/` measure 99% coverage combined**, clearing
  `14` §10's pipeline-stage floor — checked deliberately, same discipline
  as every ticket since T4.1.

### T5.1 — The LLM gateway

**Read `06` §2.4's implementation note alongside this section — it has the
per-mechanism detail; this is the decision log.**

- **Provider seam matches `GitHubGateway`'s shape exactly, for the same
  reason.** `Provider` (`ai/providers/base.py`) is a `Protocol`; the
  contract tests never see a mock standing in for a real implementation,
  they see `FakeProvider`, a structurally-real one. `15` T5.1's accept
  criterion says "*simulated* provider failure" — that word choice is what
  justified building the fake as the primary test surface rather than
  mocking the Anthropic/OpenAI SDKs directly, with real network calls
  exercised separately (`test_ai_provider_live.py`, skipped without a real
  key) and the SDK exception-mapping logic itself verified by monkeypatching
  the SDK client's own method (`test_ai_provider_{anthropic,openai}.py`) —
  a middle layer neither purely offline nor requiring network.
- **Real Anthropic and OpenAI SDKs, not a raw HTTP client.** No ADR
  addresses this choice directly; the reasoning is the same class as `ast`
  over Tree-sitter and no-tokenizer-dependency (T4.1–T4.4) — boring,
  first-class support for structured output (forced tool use / native JSON
  schema mode) that a hand-rolled HTTP client would have to reimplement
  imperfectly. `apps/worker/pyproject.toml` gained `anthropic`, `openai`,
  `psycopg[binary,pool]`, `redis`, `pyyaml`, `httpx`, `pydantic-settings` —
  all either already used elsewhere in the workspace (`apps/api` uses
  `psycopg`, `redis`, `httpx`, `pydantic-settings` for the identical
  reasons) or the obvious first-party SDK for the provider it wraps.
- **Every provider call that returns a response writes its own `llm_calls`
  row, immediately** — not one row per `complete()` invocation. A native
  attempt, a repair call, and a suspicious-content retry are each a real,
  billed round-trip; a call that fails before returning anything (rate
  limited, timed out) writes nothing, since nothing was billed. This reads
  directly off `06` §8.3 ("every LLM call writes a row") taken literally
  rather than "every gateway invocation."
- **`TenantRepository` (`11` §4 Layer 3) is implemented for the first
  time.** It existed only as pseudocode in `11` before this ticket — no
  worker ticket through Phase 7 touched Postgres at all. Adapted to this
  codebase's actual style (raw parameterized SQL via `psycopg`, matching
  `apps/api/roottrace_api/ingest/repository.py`) rather than the doc's
  ORM-flavoured sketch, since nothing in this codebase uses an ORM and the
  *principle* — no tenant-table query without an explicit `project_id`,
  enforced by `TenancyViolation` — does not depend on which query-building
  style carries it.
- **Cost is computed from what the provider actually reports, never
  estimated.** Unlike T4.4's retrieval-budget token count (`chars / 3.5`,
  deliberately conservative because no ground truth exists until the
  content is retrieved), a completed provider call already reports exact
  `tokens_in`/`tokens_out` — there is nothing left to estimate. `cost.py`
  prices per 1,000 tokens rather than per token specifically so a
  sub-$1-per-1M-token rate (the `fast` tier: $0.30 in / $1.50 out) survives
  as an exact integer instead of truncating to zero, per `CLAUDE.md`'s
  "money is an integer, never a float" rule.
- **Two real doc/code mismatches found and fixed while building this,
  neither trivial:**
  - `A3-CONFIGURATION.md` documents `RT_PIPELINE_MIN_CONTEXT_FILES`/
    `RT_PIPELINE_MIN_CONTEXT_TOKENS` as the operator override for T4.4's
    `insufficient_context` threshold, but `ranking.py` never read them —
    hardcoded module constants since T4.4 was first built, invisible until
    reading `A3` for T5.1's own config wiring surfaced it. Also, `A3`'s
    documented defaults (`3`/`800`) still matched the *original*,
    corpus-disproven threshold, not the `1`/`1` values the coordinator's
    decision had just landed — a stale default that would have silently
    reintroduced the exact threshold that was just moved off of S5. Both
    fixed in a dedicated commit before T5.1 began (`72156a2`).
  - `ai/storage.py`'s first draft invented a dedicated `prompts` bucket,
    justified by a claim ("`03`/`07` route artifacts to their own buckets")
    that turned out to be fabricated — `03` §S1 step 8 actually specifies
    one shared bucket (`RT_STORAGE_BUCKET`, default `roottrace-artifacts`)
    with path prefixes per artifact type (`raw/{project_id}/...`). Caught
    by checking the claim against the spec text before shipping it, not
    after; fixed to `PATH_PREFIX = "prompts"` inside the one configured
    bucket, and `settings.py` gained the `storage_bucket` field `A3`
    already documented.
- **Two responsibilities `06` §2.4's own table names are not built,
  disclosed rather than silently skipped** (§5 items 16–17): a
  provider-health circuit breaker independent of the B9 cost breaker (no
  section gives it an algorithm the way §8.2a gives the cost breaker one);
  and provider-side prompt caching (Anthropic `cache_control` / OpenAI
  automatic caching) — needs the L1–L5 prompt-layer boundary T5.2
  introduces, since `RenderedPrompt` here only carries `system`/`user`.
  The *deterministic* content-hash cache (a different mechanism, same
  table row) is built and tested.
- **Worker gets its first typed `Settings`** (`apps/worker/roottrace_worker/
  settings.py`), deliberately duplicated from `apps/api/roottrace_api/
  settings.py` rather than shared — same reasoning as the SDK's duplication,
  since the two packages have never declared a dependency on each other and
  are separate deployables with separate privilege boundaries. Kept the
  `extra="forbid"` + unknown-`RT_*`-variable boot scan `api`'s version has,
  even though this file's own field set is narrower — the worker holds
  *more* sensitive credentials than `api` (the service-role key, provider
  keys), so a silently-ignored typo'd env var is a worse failure mode here,
  not a lesser one.

### T4.4 — Ranking, budget, quality scoring — the finding, and its resolution

**Read this section in full before doing anything else with Phase 7 or 8.**

- **The mechanism is a correct, literal implementation of `03` §S5.** The
  relevance formula (`strategy_weight × recency_factor × proximity_factor ×
  (1 + 0.15 × symbol_overlap)`), the nine-tier eviction priority, and the
  24,000-token hard budget are all built as specified, in
  `apps/worker/roottrace_worker/pipeline/retrieve/{ranking,quality,tokens,
  bundle}.py`, with 99% coverage on the package (98–100% per file) and 77
  corpus-level tests plus 37 unit tests. The `insufficient_context` threshold
  was originally built exactly as spec'd too ("fewer than 3 distinct
  priority 1–4 files or fewer than 800 in-app tokens") — the numbers below
  are what that literal build measured against the corpus, and why they
  were revised.
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
- **The finding.** Measuring T4.4's own acceptance criteria against the full
  25-case corpus:

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
  is genuinely only one file's worth of directly-implicated code.
- **First response: stopped, not tuned.** Per direct instruction — "do not
  adjust the threshold, do not tune the code to fit this corpus
  specifically, do not move on anyway" — the threshold was left exactly as
  spec'd, the finding was written up in full (this section, `03` §S5, `15`
  T4.4), and the session halted overnight for the coordinator's review rather
  than picking a direction unilaterally.
- **The coordinator's direction, the next morning: "loosen the threshold for
  V1."** Before writing any code against it, the exact numbers were checked
  to see what "loosen" could mean. They ruled out the obvious approach:
  `external-03` (a real bug — `InventoryClient.reserve` has no circuit
  breaker) and `unfixable-01` (a control — the same function already handles
  the failure correctly and raises a typed, named error) admit the
  **identical** 2 files and 1231 tokens of priority 1–4 evidence. `config-01`
  (also a real bug) admits only 251 tokens — fewer than either control. No
  single file-count or token-count number admits `config-01` while also
  rejecting both controls; the fixable and control cases' evidence volumes
  fully interleave. This was brought back to the coordinator as a second,
  more specific question rather than silently picked around.
- **The resolution, decided by the coordinator (not tuned in unilaterally):**
  judging *fixability* from evidence volume was never S5's question to
  answer. `03` §S6 already has its own `insufficient_context` exit ("on
  evidence-binding failure … terminal `insufficient_context`") for exactly
  the case where a model concludes "no defect, external cause" — that
  judgment belongs to reasoning, not to a mechanical count at retrieval.
  `MIN_ADMITTED_FILES` and `MIN_ADMITTED_IN_APP_TOKENS` in `ranking.py` are
  lowered from `3`/`800` to `1`/`1` — S5's bar becomes "did retrieval resolve
  the failure point with any real content," which is the only question S5 can
  honestly answer. Under that bar, **all 25 corpus cases — the 23 fixable
  cases and both controls — correctly produce a `ContextBundle`**: retrieval
  genuinely succeeded for all 25, including the controls, whose
  `InventoryClient` code is real and retrievable and simply contains no
  defect. `03` §S5's spec text and implementation note, `15`'s T4.4 entry,
  and `tests/integration/test_retrieve_ranking_corpus.py` were all updated in
  the same commit as the code change.
  - The corpus test suite for the controls changed shape accordingly:
    `test_the_controls_terminate_as_insufficient_context` (S5-only) is
    replaced by `test_the_controls_retrieve_the_client_boundary_correctly`,
    which checks that S5 found the right code for the right reason (the
    `InventoryClient` boundary is in the bundle), not that S5 abstains.
    Whether the controls are *fixable* is now a **Phase 8 (S6) acceptance
    property**, not T4.4's — `03` line 751 already gives S6 the mechanism to
    determine that once it exists.
  - A latent test bug surfaced by this change, fixed in the same pass:
    `test_priority_1_and_2_are_never_evicted` compared
    `understanding.failure_point.repo_path` (S4's unverified cascade
    steps 1–2 output) directly against admitted paths. For `config-02`
    specifically that raw path (`services/services/export.py`) is not a real
    file — T4.2/T4.3 always re-verify it against the tree before admitting
    anything (`resolve_against_tree`), but the test never had, because
    `config-02` never reached `ContextBundle` status under the old threshold
    to exercise this check. Fixed by resolving the same way production code
    does (`strategies._resolve_failure_path`) before comparing.
- **Everything that could be verified honestly was.** Coverage floor
  (`14` §10, ≥95% for retrieval) checked deliberately for `pipeline/retrieve`
  specifically, not inferred from the general 75% ratchet — same discipline
  as every phase before this one. 99% held after the threshold change.

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

**Item 13 was the reason Phase 7 was halted overnight; it is now resolved.
Kept in this table as the record of the finding and the decision, not as an
open blocker. Everything else in this table is routine carry-forward, same
as any other session.**

| # | Item | Owner ticket | Status |
|---|---|---|---|
| 13 | ✅ **`03` §S5's `insufficient_context` threshold could not distinguish fixable cases from designed controls by evidence volume — resolved by moving that judgment to S6** | T4.4 → decided by the coordinator | See §4 T4.4 above for the full write-up. The original threshold ("<3 distinct files or <800 tokens") wrongly terminated 18/23 fixable cases; loosening the numbers turned out to be impossible, since `external-03` (fixable) and `unfixable-01` (control) admit the identical evidence volume. **Decision:** S5's threshold is lowered to "did retrieval resolve the failure point with real content" (`MIN_ADMITTED_FILES = 1`, `MIN_ADMITTED_IN_APP_TOKENS = 1`); judging fixability moves to S6's existing `insufficient_context` exit (`03` line 751), not yet built. All 25 corpus cases now correctly reach a `ContextBundle` at S5. The controls' own fixability determination is now a **Phase 8 acceptance property** — when S6 is built (T5.3), confirm it independently classifies `unfixable-01`/`unfixable-02` as `insufficient_context` and the other 23 as proceeding, which is the property the old S5-only test used to check. |
| 1 | **Refresh-token reuse detection does not work** | T1.4 | Replaying a consumed refresh token returns 200 at `GOTRUE_SECURITY_REFRESH_TOKEN_REUSE_INTERVAL` of both 10 and 0, suggesting 0 means *unlimited* in this build. A stolen refresh token is replayable; `11` T15's mitigation is incomplete. |
| 2 | **S1 p95 budget not met** | T2.1 | Linux CI: median 28 ms, **p95 226 ms** against a 50 ms target. Bimodal — ~15 samples at 26–33 ms, 5 at 94–230 ms. An unidentified periodic stall, so a real defect rather than platform cost. `xfail(strict=False)` with the numbers in the reason. |
| 3 | **Object storage (S1 step 8) not implemented** | T2.1 → worker | `payload_url` is null. The `api` holds no credential that can write to Supabase Storage, by the same boot invariant that keeps the service-role key out of it. The archive write belongs to the worker. |
| 4 | **Triage is not DB-wired** | T2.4 → T8.2 | The B8 insert-and-handle-conflict path needs the `investigations` table and its partial unique index, which belong to the orchestrator. |
| 5 | **Fixture tree has one revision on disk** | T3.3 | `ref` is resolved, validated and recorded but does not select content. `blame` and `compare` do distinguish revisions. Nothing in V1 reads a historical ref. |
| 6 | **Fixture suite verified against stock `python:3.12-slim`, not the hardened image** | T3.1 → T6.1 | Checked with `--network none`, which is the property T6.1 would otherwise inherit as a surprise. Re-verify at T6.1. |
| 7 | **`replay` and `live` transports raise `TransportUnavailable`** | V2 | Deliberate. They are listed and skipped in the contract suite rather than omitted — an omitted transport is one nobody remembers to add. |
| 8 | ✅ **The LLM structured-extraction step of S4 is unimplemented** | T4.1 → T5.2, closed | `GatewayExtractor` (T5.2) is the real `StructuredExtractor` implementation — `UnavailableExtractor` remains as the fallback `03` §S4's own failure-mode table specifies (provider exhaustion, budget exhaustion), not as the only implementation. Tested end-to-end through `understand(...)` against `FakeProvider`. What remains is orchestration, not extraction — see item 15. |
| 9 | **Exception-family accuracy has no margin (23/25, exactly the T4.1 bar)** | T4.1 → T5.2 | `race-01` and `resource-01` are knowable only from breadcrumbs, and the deterministic taxonomy deliberately never reads them (`A1` §9). The extractor at T5.2 is expected to close this; if it does not, the threshold needs revisiting, not the taxonomy. Named explicitly in `tests/integration/test_understand_corpus.py` so a third miss is a build break. |
| 10 | **`POST /v1/repositories/{id}/test_path_mapping` (`05` §6.6) is not wired as an HTTP endpoint** | T4.2 → Phase 16 or first `repositories`-CRUD ticket | `dry_run_path_mapping` is the full resolution logic as a pure function; the route needs `repositories` CRUD, which no ticket through Phase 7 builds. Deliberate scoping decision, not a gap — see `15` T4.2 and `05` §6.6. |
| 11 | **Three corpus cases have no reachable root cause under T4.3's four strategies** | T4.3 → T4.4 / T5.x | `regression-02` (2 hops away), `config-02` (root cause is a composition-root-injected value's producer, no call edge), `type-mismatch-03` (root cause and failure point are sibling functions sharing only data, no call edge). Named by case id in `tests/integration/test_retrieve_strategies_corpus.py::ROOT_CAUSE_UNREACHABLE_BY_T4_3`; whether T4.4's budget allowing a second hop, or S6's reasoning-driven follow-up retrieval, closes any of these is an open question for later phases. |
| 12 | **Release correlation (`03` §S5 strategy D) has no automatic "previous release" lookup** | T4.3 → T8.2 or a `repositories`/releases data source | `strategy_d_git_history`'s `release_diff` is `None` unless the caller supplies `previous_ref` explicitly — the mechanism (`gateway.compare`) is built and tested, but nothing upstream yet knows what the previous release tag was; that needs a releases table or GitHub API call this ticket has no reason to add. |
| 14 | **S6 must independently reproduce the fixable/unfixable split item 13 moved off of S5** | T5.3 | Now that S5 admits all 25 corpus cases as `ContextBundle`, nothing yet asserts that S6 classifies `unfixable-01`/`unfixable-02` as `insufficient_context` (their `expected.final_status`) while the other 23 proceed. This is the acceptance property `test_the_controls_terminate_as_insufficient_context` used to check at S5 before item 13's resolution moved it to S6 — T5.3's own corpus test should assert it directly once S6 exists, not assume it. |
| 15 | **No orchestration constructs a live `GatewayExtractor`/`LLMGateway`, and no `Settings`-driven factory exists to build one** | T5.1/T5.2 → wherever first runs a real investigation | `apps/worker/roottrace_worker/settings.py` declares every field the gateway needs (API keys, cache TTL, cost caps); `gateway.py`/`GatewayExtractor` take them all as constructor arguments. Every current caller (all of them tests) constructs both directly with explicit providers/routing/storage/db/redis objects and a hand-picked `project_id`. T5.2 proved the *mechanism* works end-to-end against `FakeProvider` — what is still missing is (a) a factory that builds a real `LLMGateway` from `Settings` with real `psycopg`/`redis` connections, and (b) the ARQ task that would construct a `GatewayExtractor` per investigation with real IDs and call `understand(event, extractor=...)` for an actual error. Neither exists yet; both are pipeline orchestration, out of scope for every ticket built so far. |
| 16 | **No provider-health circuit breaker** | T5.1 → undecided | `06` §2.4's responsibilities table names one ("opens ... when a provider exceeds its error threshold"), independent of the B9 cost-cap breaker (built). No section gives it an algorithm the way §8.2a gives the cost breaker one, so nothing was built against a guess. `_dispatch_tier` walks the configured tier order fresh on every call with no memory of a provider's recent failures. |
| 17 | **No provider-side prompt caching** | T5.1/T5.2 → undecided | `06` §2.4/§3.1 name provider-native caching of the static L1-L3 prompt layers (Anthropic `cache_control`, OpenAI automatic caching) as a cost/latency optimisation, distinct from the *deterministic* content-hash cache (built, `ai/cache.py`). T5.2's `assemble_prompt` builds the L1-L5 split *conceptually* (`system`/`domain`/`task` are separate parameters), but the layers are concatenated into one flat `system` string before reaching `RenderedPrompt` — a provider call has no way to mark only the static L1-L3 span cacheable from a single string. Closing this needs `RenderedPrompt`/`ProviderRequest` to carry structured, per-layer content blocks, not just two flat strings; not attempted here since no ticket has scoped that shape change yet. |

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

**Phase 7 is complete and cleared (all four tickets, item 13 resolved — see
§5). Phase 8 (`15` §7) is underway: T5.1 and T5.2 done, T5.3 next.**

| Ticket | Scope | Status |
|---|---|---|
| T4.1 | Stage 4 — `understand` | ✅ Done — `apps/worker/roottrace_worker/pipeline/understand/` |
| T4.2 | Frame path resolution | ✅ Done — `apps/worker/roottrace_worker/pipeline/retrieve/path_resolution.py`; corpus at 25/25 |
| T4.3 | Stage 5 — retrieval strategies A, B, D, E | ✅ Done — `apps/worker/roottrace_worker/pipeline/retrieve/strategies.py` |
| T4.4 | Ranking, budget, and quality scoring | ✅ Done — all bars clean; threshold revised per §5 item 13 |
| T5.1 | LLM gateway | ✅ Done — `apps/worker/roottrace_worker/ai/*.py`; all 3 accept bars clean; see §4's T5.1 section and §5 items 16–17 for disclosed gaps |
| T5.2 | Prompt system | ✅ Done — `apps/worker/roottrace_worker/ai/prompts/*`; both accept bars clean; closed §5 item 8; see §4's T5.2 section for the two T5.1 bugs it found and fixed |

**T5.3 — Stage 6 `reason`** (`15` §7, next): five-step protocol, hypothesis elimination,
evidence binding with post-validation (P2 — every claim binds to a file path,
line range, or commit SHA, verified by literal string comparison), retry-
once-then-terminate on binding failure (the `insufficient_context` exit item
13 moved the controls' fixability judgment onto — see §5 item 14). Accept:
≥20/25 fixtures identify the correct root-cause file; 100% of surfaced
findings pass evidence validation; a deliberately fabricated citation is
rejected; **and** (item 14) `unfixable-01`/`unfixable-02` terminate as
`insufficient_context` while the other 23 proceed — the property T4.4's own
corpus test used to check before item 13 moved it here.

`reason/v3.md` already exists (T5.2, verbatim from `A2` §4) — T5.3 does not
need to author prompt text, only the `RootCauseAnalysis` Pydantic contract
(`03` §S6's output shape), the semantic post-validators `06` §4.2's table
names for S6 specifically, and a `GatewayReasoner`-shaped caller matching
`GatewayExtractor`'s pattern. Item 15's orchestration gap (no ARQ task
constructs any of these with real IDs yet) applies here too — worth
deciding once, for both S4 and S6, rather than solving it twice.

**Standing rules that still apply:** finish each ticket's acceptance
criteria before starting the next; commit and push after each ticket
individually with CI green; update the relevant doc in the same commit as
the code it describes; a bug fix ships with a test that fails before the fix.
