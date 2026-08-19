# RootTrace AI — Project Status

> **Not a specification.** Every other file in `docs/` is binding; this one is a
> snapshot. It records where the build actually is, what is proved, what is
> open, and what to pick up next. Deliberately un-numbered so it is never
> mistaken for part of the frozen contract set.
>
> **Last updated:** 2026-08-19, this commit (T6.4 — the nine gates — built;
> T6.1 sandbox image, T6.2 orchestration, and T6.3 isolation all built the
> same session; Phase 10's mechanism is now complete through G0–G8 —
> T6.4a's real-p95 measurement across all 25 fixtures and T6.5's degraded
> mode are what remain, see §1). Phase 8 (AI reasoning) and Phase 9 (patch
> generation) were completed the prior day, both mechanism-complete with
> their corpus-wide accuracy bars deferred to `T10.1`. T4.4's calibration
> finding resolved by the coordinator before any of that; Phase 7 cleared
> first — see §5 item 13. Every T6.x finding in this entry was made by
> testing against a real Docker daemon, not assumed from a config dict —
> see §4's T6.1–T6.4 sections for the corrections that testing surfaced.
> Regenerate this from `docs/15-V1-BUILD-PLAN.md` and `git log` — those are the
> authorities. If this file and `15` disagree, `15` wins.

---

## 1. Where the build is, in one line — READ THIS FIRST

**Phase 7 (retrieval) is complete and cleared. Phase 8's and Phase 9's
mechanisms are complete (T5.1–T5.4), both phases' corpus-wide accuracy
bars deferred to `T10.1`. Phase 10 (sandbox validation) is now
mechanism-complete through G0–G8: T6.1 (container image), T6.2
(orchestration), T6.3 (isolation, the full `07` §12 security checklist),
and T6.4 (the nine gates) are all built, tested against a real Docker
daemon, and committed. Two items remain before Phase 10 itself is done: T6.4a (measuring real
sandbox p95 across all 25 fixtures × 3 runs — a corpus-wide measurement,
same shape as the accuracy bars deferred above) and T6.5 (degraded mode).
T6.5 is next.**

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

**T5.3, Stage 6 `reason` (`apps/worker/roottrace_worker/pipeline/reason/*`):**
the second `StructuredReasoner` seam (mirroring T4.1/T5.2's `understand`
pattern) — `GatewayReasoner` assembles a full five-layer prompt from a
`ContextBundle` plus `ErrorUnderstanding`, calls `LLMGateway` on the
`reasoning-a` tier, and validates every claim in the reply against `03`
§S6's evidence-binding rule before it's trusted: a reasoning step or
eliminated hypothesis with **no declared evidence** is kept as-is
(speculative, matches `03`'s own worked example); one that **declares**
evidence and gets **any** of it wrong is **dropped entirely**, not
partially. `fix_strategy.files_to_modify` is filtered to retrieved paths.
A second, S6-specific retry ladder — distinct from T5.1's JSON-schema
ladder — re-dispatches once with a correction prompt quoting the model's
own prior reply when the primary root-cause finding (a surviving
`conclude` step plus a grounded `fix_strategy`) fails to bind; a second
failure terminates the investigation as `insufficient_context` via
`ReasonerUnavailable`, reusing `retrieve.bundle.InsufficientContext` as
S6's terminal shape rather than inventing a stage-specific one.
`model`/`prompt_version`/`tokens` on the trusted `RootCauseAnalysis` are
injected from the real `LLMResult` the gateway returns, never taken from
the model's own JSON self-report. 99% coverage on `pipeline/reason`
(344 stmts), tested against `FakeProvider` for the mechanism (evidence
binding, the retry ladder, a deliberately fabricated citation rejected)
plus one live-gated test (`test_reason_live.py`, 7 real fixture cases,
skipped without `RT_ANTHROPIC_API_KEY`) that checks the mechanism does not
fall over against a real model — **not** an accuracy measurement.

**Explicit accuracy-bar deferral, by coordinator decision:** `15` T5.3's
two corpus-wide statistical accept criteria — `≥20/25` fixtures identify
the correct root-cause file, and `unfixable-01`/`unfixable-02` terminate as
`insufficient_context` while the other 23 proceed — are **not** verified by
T5.3. T5.3 proves the mechanism runs correctly (evidence binding holds,
fabrication is rejected, the retry ladder works, a real model call doesn't
crash it); it does not prove the `≥20/25` number, which needs the full
25-case corpus, multiple runs, and a real evaluation harness to measure
honestly. That measurement stays exactly where `15` already puts it:
`T10.1`, Phase 15. Nobody should read T5.3 as having already closed this
bar — see `15` T5.3's own accept-criteria note and §5 item 14 below.

**T5.4, Stage 7 `patch` (`apps/worker/roottrace_worker/pipeline/patch/*`):**
the third `Structured*` seam (`GatewayPatcher`, mirroring T5.2/T5.3's
pattern) — assembles a five-layer prompt from S6's `RootCauseAnalysis`
(specifically `fix_strategy`) plus the `ContextBundle`, calls `LLMGateway`
on `reasoning-a`, and runs `03` §S7's full "Constraints enforced on the
output" table deterministically before trusting a reply: an in-memory
`unidiff`-based apply check against the actual retrieved window (H5) —
`bundle.files` or, for an existing test file, `bundle.tests.found` — and a
scope check (H6) covering the `files_to_modify`/regression-test allowlist,
forbidden CI/lockfile paths, `fix_strategy.must_not_modify`, and existing-
test deletion. `patch_id`/`base_commit`/`files_changed`/`scope_warning`/
`model`/`prompt_version`/`tokens` are all either caller-supplied or
computed by this stage's own code, never trusted from the model's JSON —
T5.3's rule, extended to every field this ticket can independently know.
Two registered failure codes rather than one (`RT-AI-0005` scope
violation, `RT-AI-0006` non-applying diff), both retried once via the same
second-ladder shape T5.3 introduced. H4 (Tree-sitter symbol existence) is
**not built** — disclosed, not silently skipped, see §5 item 18. 99%
coverage on `pipeline/patch` (360 stmts), tested against `FakeProvider`
plus one live-gated test (`test_patch_live.py`, 5 real fixture cases
running the full S4→S5→S6→S7 chain) that checks the mechanism does not
fall over against a real model — **not** an accuracy measurement, same
explicit deferral as T5.3's, applied consistently: `15` T5.4's
`≥24/25 diffs apply cleanly` bar is verified at `T10.1`, not here.

**T6.1, the sandbox image (`apps/sandbox-runner/`):** `roottrace/sandbox-
python:3.12`, digest-pinned base, an offline wheel cache covering the V1
fixture corpus's own dependencies plus the pinned analysis toolchain
(pytest, ruff, mypy, bandit, coverage), and the new
`roottrace_sandbox_runner` package that becomes the image's `ENTRYPOINT`.
**Two real corrections to `07`'s own B10 mechanism, both found by testing
against a live Docker daemon rather than trusting the doc:** `docker cp`
is rejected outright against any container created with `read_only: true`
— input now travels over the container's stdin after `start()` instead;
`/work`'s tmpfs content does not survive the container's own process
exiting, so `runner.py` now also emits the result JSON to stdout,
delimited, which `docker logs` retrieves reliably from a stopped
container. Neither correction relaxed any isolation control — see T6.3.

**T6.2, orchestration (`apps/worker/roottrace_worker/pipeline/validate/`):**
`SandboxOrchestrator` — create, start, write stdin, wait with a hard
SIGKILL timeout, extract the delimited result, always remove — plus
`SandboxReaper` for a container whose supervising worker process itself
died mid-validation. Every `07` §3 isolation flag is set on every
container from creation; verified live: a clean run round-trips, a 0 s
timeout forces the kill path and still cleans up, the concurrency
semaphore caps real *running* containers at the configured limit
(measured by polling actual container state, not task lifecycle — an
easy thing to get wrong, gotten wrong once before switching to the real
measurement), and the reaper removes a genuinely orphaned container while
leaving a fresh one alone. 100% coverage on `pipeline/validate` (193
stmts at the time) combining unit and integration suites.

**T6.3, isolation (`apps/worker/tests/test_sandbox_isolation_security.py`):**
every item in `07` §12's checklist as a real assertion against a live
container — DNS/TCP blocked, `EROFS` on `/etc`, `mount`/`unshare`/
`ptrace(ATTACH)` all `EPERM`, a fork bomb contained without host impact,
an over-limit allocation OOM-killed inside the container only, an
infinite loop SIGKILLed at the timeout, no unexpected host path under
`/proc/self/mountinfo`, an uncached `pip install` failing offline. **One
real, non-security-critical finding, corrected rather than kept:** the
checklist's original name-pattern regex for the container's environment
flagged `GPG_KEY` (a public release-signing key fingerprint baked into
the base image, not a credential — Docker's `Env` merges with an image's
own `ENV`, never replaces it, so nothing can unset it). Replaced with an
explicit allowlist (`KNOWN_BASE_IMAGE_ENV_KEYS`) plus a direct check that
no actual worker secret name is present — the invariant `07` §3 L7
actually cares about.

**T6.4, the nine gates (`pipeline/validate/gates.py` + `roottrace_sandbox_
runner/gates.py`):** G0/G1 host-side (diff-apply-and-materialise, `ast`-
based syntax check), G2–G8 inside the container, dispatched fail-fast in
`07`'s own cheapest-first order. **G4 — "the critical gate" — verified
with the actual `ValidationResult` printed, not just an assertion:** a
genuine fix with a genuinely reproducing test passes every gate; a
theatrical test that also passes on unpatched code is rejected; a test
that fails for an unrelated reason (a broken import, not the bug) is
rejected too, with the real exception type and message in the gate
detail either way. G5 catches a "fix" that doesn't fix anything. G6's
pre/post baseline correctly separates `newly_failing` from
`already_failing`. G7 counts only new static findings, HIGH-only gate-
failing. G8 reconstructs added lines via `difflib` (no raw diff travels
into the container) and blocks a newly-introduced `eval()`. Disclosed
scoping decision: G4/G5's exception-family check is strict only for the
`03` §S4 families that map cleanly onto specific built-in exceptions;
the families a codebase mostly defines its own exception classes for are
recorded, not gate-failing.

**Every isolation control CLAUDE.md names as non-negotiable — no network,
no credentials, read-only rootfs, non-root, all capabilities dropped —
held throughout T6.1–T6.4, confirmed against a live container, never
loosened to make anything work.** Every correction found along the way
(stdin/stdout delivery, the environment allowlist, `existing_tests`
needing content) was a fix to *how* a property was achieved, never a
reduction of the property itself — each one disclosed here and in `07`,
not silently patched over.

The system, as built: accept a production error over HTTP, sanitise it,
fingerprint it, group it into an issue, score its severity, decide whether it
deserves a pipeline run, turn it into a structured `ErrorUnderstanding` with a
retrieval plan, resolve every stack frame in the corpus to the real file it
came from, assemble frame-direct content plus a one-hop call graph plus git
history plus a discovered test, rank/dedupe/budget all of that into a real
`ContextBundle` for all 25 corpus cases, turn any of that into a real,
structured, cost-accounted, failover-safe chain of LLM calls that produces
an evidence-bound root-cause finding **and** an in-memory-verified unified
diff, and can now **prove that diff** against a hardened, isolated
container running the real nine-gate sequence `03` §S8 specifies — G4's
regression-test-must-actually-reproduce-the-bug requirement chief among
them. S4's `understand`, S6's `reason`, and S7's `patch` all genuinely
call a model end-to-end (`GatewayExtractor`, `GatewayReasoner`,
`GatewayPatcher`, each tested against `FakeProvider` plus one live-gated
smoke test); S8's `validate` genuinely runs a real diff through a real
sandbox. Nothing yet constructs any of S4–S8 for a live investigation
(no orchestration ticket exists). No vector search (deferred to V2 by
design), no repair loop, no independent review, no confidence engine, no
dashboard yet.

**Next:** T6.5 (degraded mode, Phase 10) — per `15` §8. See §8. T6.4a
(real sandbox p95 across all 25 fixtures × 3 runs) remains open, same
"corpus-wide measurement deferred" shape as `T10.1`'s own bars — see §5.

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
| 8 | AI reasoning | T5.1–T5.3 | 🔶 Mechanism complete — all 3 tickets built; accuracy bar deferred to T10.1, see §5 item 14 |
| 9 | Patch generation | T5.4 | 🔶 Mechanism complete — built; accuracy bar deferred to T10.1, see §5 item 19 |
| 10 | Sandbox validation | T6.1–T6.5 | 🔶 Mechanism complete through G0–G8 (T6.1–T6.4 built); T6.4a real-p95 measurement and T6.5 degraded mode remain |
| 11 | Repair loop | T7.1 | ⬜ Not started |
| 12 | Independent review | T7.2 | ⬜ Not started |
| 13 | Confidence engine | T7.3 | ⬜ Not started |
| 14 | Fixture GitHub transport | T8.1 | ⬜ Not started |
| 15 | Evaluation harness | T10.1 | ⬜ Not started |
| 16 | Dashboard | T8.2–T8.4, T9.1–T9.8 | ⬜ Not started |

**25 tickets closed of 47** (T6.4 is the 25th), **Phase 7 cleared, Phase 8
and Phase 9 both mechanism-complete** (both accuracy bars deferred to
T10.1), **Phase 10 mechanism-complete through G0–G8**. See §1. (39 tickets
have their own section in `15`; T9.1–T9.8 are listed as a table in `15`
§11.)

Of the 14 pipeline stages in `03`, **S1–S8 all exist**, S5 in full: frame
path resolution, four of five fetch strategies (strategy C deliberately
deferred to V2 — the index is never populated in V1), and ranking/dedup/budget/
quality scoring. **S4's, S6's, and S7's seams are all closed** —
`GatewayExtractor` (T5.2), `GatewayReasoner` (T5.3), and `GatewayPatcher`
(T5.4) are real `StructuredExtractor` / `StructuredReasoner` /
`StructuredPatcher` implementations, each assembling the five-layer prompt
(`ai/prompts`, T5.2) and calling `LLMGateway.complete` (T5.1), each tested
end-to-end against `FakeProvider` plus one live-gated smoke test against a
real model. **S8 (`validate`) is real too** — `SandboxOrchestrator` drives
the real `roottrace/sandbox-python:3.12` image through a real G0–G8
sequence, proven against a live Docker daemon rather than mocked. What is
still missing for any of S4–S8 to run against a real investigation is
orchestration — nothing yet constructs any of them with real IDs and
wires S4 → S5 → S6 → S7 → S8 together for a live error (no orchestration
ticket exists). See §4.

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
| S6 `reason` (T5.3) | `GatewayReasoner` — the real `StructuredReasoner`, closing S6's seam; evidence-binding validator (`validate.py`) enforcing `03` §S6's rule that a claim declaring evidence is dropped whole on any binding failure, while a claim declaring none is kept as speculative; a second, S6-specific correction-retry ladder distinct from T5.1's schema ladder; `model`/`prompt_version`/`tokens` injected from the real `LLMResult`, never model self-report | `apps/worker/tests/test_reason_validate.py` (22), `test_reason_gateway_reasoner.py` (12), `test_reason_stage.py` (5), `test_reason_live.py` (7, live-gated, skipped without `RT_ANTHROPIC_API_KEY`) — mechanism criteria hold (100% evidence validation, fabricated citation rejected); **the corpus-wide `≥20/25` and unfixable-01/02-vs-23 accuracy bars are explicitly deferred to `T10.1`, not verified here — see §1 and §5 item 14** |
| S7 `patch` (T5.4) | `GatewayPatcher` — the real `StructuredPatcher`, closing S7's seam; `diffing.py`'s in-memory `unidiff`-based apply check (H5) against the actual retrieved window; `validate.py`'s scope check (H6) — allowlist, forbidden CI/lockfile paths, `must_not_modify`, existing-test deletion; two registered failure codes (`RT-AI-0005` scope, `RT-AI-0006` non-applying diff), both retried once; `patch_id`/`base_commit`/`files_changed`/`scope_warning`/`model`/`prompt_version`/`tokens` all caller-supplied or computed, never model self-report | `apps/worker/tests/test_patch_diffing.py` (11), `test_patch_validate.py` (14), `test_patch_gateway_patcher.py` (8), `test_patch_stage.py` (5), `test_patch_live.py` (5, live-gated, skipped without `RT_ANTHROPIC_API_KEY`) — mechanism criteria hold (every constraint-table row exercised, both failure codes fire and retry correctly); H4 (Tree-sitter symbol existence) disclosed as not built, §5 item 18; **the corpus-wide `≥24/25 diffs apply cleanly` bar is explicitly deferred to `T10.1`, not verified here — see §1 and §5 item 19** |
| Sandbox image (T6.1) | `roottrace/sandbox-python:3.12` — digest-pinned, offline wheel cache, `roottrace_sandbox_runner` as `ENTRYPOINT`. Two `07` B10 corrections (stdin in, delimited-stdout result out) found by testing against a live daemon | `apps/sandbox-runner/tests/` (20 tests, container-free) — image builds reproducibly; `fixtures/synthetic-repo/requirements.txt` installs `--no-index` under `--network none`, verified live |
| Sandbox orchestration (T6.2) | `SandboxOrchestrator` (create → stdin → wait-with-kill → extract → remove) + `SandboxReaper`; every `07` §3 isolation flag set from `create()` | `apps/worker/tests/test_validate_orchestrator*.py` (18) — clean run, 0 s timeout kill, concurrency cap measured against real running containers, reaper reaps an orphan and leaves a fresh one alone |
| Sandbox isolation (T6.3) | Every `07` §12 checklist item as a live assertion — network, filesystem, identity, syscalls, resource limits, mountinfo | `apps/worker/tests/test_sandbox_isolation_security.py` (17, 1 skipped as a named duplicate of T6.2's own proof) — **one real, disclosed finding**: `GPG_KEY` (public, not a secret) failed the checklist's original name-pattern regex; replaced with an explicit allowlist plus a direct worker-secret-name check |
| The nine gates (T6.4) | G0/G1 host-side (`pipeline/validate/gates.py`); G2–G8 in-container (`roottrace_sandbox_runner/gates.py`), fail-fast, `07`'s own order. **G4 verified with the real `ValidationResult` printed**: genuine fix passes, theatrical test rejected, unrelated-error test rejected | `apps/sandbox-runner/tests/test_gates_pure.py` (23, pure logic), `apps/worker/tests/test_sandbox_gates_integration.py` (11, live container) — all four `15` T6.4 accept criteria verified individually, not deferred (mechanism claims, not a corpus-wide statistic) |

**Test totals:** 2,247 collected — 1,221 `unit`, 1,009 `integration`; 237 tests
also carry the `security` marker. Overall unit coverage **91%** against a ratchet
of **75**; `pipeline/understand`, `pipeline/retrieve`, `pipeline/reason`,
`pipeline/patch`, `ai/`, and `pipeline/validate`'s host-side half are all at
91–100% — clearing `14` §10's ≥90%/≥85% pipeline-stage floor. `roottrace_
sandbox_runner/gates.py`'s G2–G8 bodies execute inside a container process
during integration testing — a different OS process entirely, invisible to
`coverage.py`'s instrumentation of the host pytest run — so their coverage
percentage understates real verification; the 11 live-container tests in
`test_sandbox_gates_integration.py` are what actually proves them correct,
which a line-coverage number on subprocess-shelling code could not do
regardless of its value.

---

## 4. Decisions taken in this session

This session covers all four Phase 7 tickets (T4.1–T4.4), all three of
Phase 8's tickets (T5.1, T5.2, T5.3), Phase 9's one ticket (T5.4), and
Phase 10's first four tickets (T6.1–T6.4).

### T6.4 — The nine gates

**Read `03` §S8 and `07` §6 in full before touching this section — both
are the binding gate contract.**

- **G0/G1 host-side, G2–G8 in-container, fail-fast in `07`'s own order.**
  `pipeline/validate/gates.py` gained `check_diff_applies` (G0 — applies a
  diff against *full file content*, not a retrieval window; T5.4 only
  ever checked applicability, never produced patched text, so
  `pipeline/patch/diffing.py` gained `apply_diff_to_files` to actually do
  that) and `check_syntax` (G1, `ast.parse`, same reasoning `ast_index.py`
  gives for not using Tree-sitter in a Python-only V1). `roottrace_
  sandbox_runner/gates.py` gained `gate_dependencies` through
  `gate_security_scan` for G2–G8, dispatched from `runner.py`'s now-filled
  `_GATE_DISPATCH`. The sequence stops at the first failing gate —
  `07` orders G2–G8 cheapest/most-informative first for exactly this
  reason, and there is no reason to run G7 against code that already
  failed G3.
- **G4 got the most scrutiny, on purpose — see §1 for the actual printed
  `ValidationResult` from all three cases.** A genuine fix with a
  genuinely reproducing test passes every gate. A theatrical test (passes
  even on unpatched code) is rejected, `failed_gate: "G4"`, with the real
  pytest summary in the detail. A test that fails for an *unrelated*
  reason (`ModuleNotFoundError` from a typo, not the bug) is also
  rejected — `07`'s own distinction ("the test is broken, not
  demonstrative") — with the real exception type surfaced, not conflated
  with a genuine reproduction.
- **Exception-family matching is strict only where a fixed list can be
  honest.** `03` §S4's nine families (plus `unclassified`) map cleanly
  onto specific built-in exceptions for five of them
  (`null_undefined`/`type_mismatch`/`key_index`/`resource`/
  `serialization`); the other five (`integration`/`data_db`/`auth`/
  `concurrency`/`unclassified`) cover exceptions a real codebase mostly
  defines its own classes for (a custom `TaxServiceUnavailable`, an ORM's
  own hierarchy) that no fixed built-in list could enumerate without
  rejecting perfectly good regression tests on a technicality. The
  family is recorded either way; it only gates G4 for the five strict
  families.
- **`existing_tests` corrected from `07`'s literal `["path"]` to
  `{path: content}`.** G6 ("tests discovered by S5 as covering the
  implicated symbols") runs tests the diff never touched at all — that is
  the entire point, catching a regression in code the patch didn't mean
  to affect — so their content cannot be assumed to already be in
  `files_original`/`files_patched`, which are scoped to only the files
  the diff *does* touch. The container has no other way to obtain it (no
  network, no host mounts). `07`'s own worked example also had
  `expected_error_family: "type_error"`, which was never one of `03`
  §S4's real family values — corrected to `type_mismatch` in the same
  pass, `RegressionTestRef.expected_error_family` now typed against the
  real `ExceptionFamily` enum instead of a bare string.
- **G8 reconstructs added lines with `difflib`, not a diff it doesn't
  have.** No raw diff text travels into the container (`SandboxInput`
  only carries `files_original`/`files_patched`/`new_files`) — G8 diffs
  the two itself and pattern-scans only the `+`-prefixed lines, matching
  `07`'s "added lines only" scope without needing a field nothing else in
  the contract has a use for.
- **`gate_dependencies` (G2) uses `pip install --user`, not `--target`** —
  `site.ENABLE_USER_SITE` makes subsequent `python` invocations pick the
  installed packages up automatically, no `PYTHONPATH` threading needed
  between gates that each materialise their own tree.
- **99% combined coverage on the host-visible half** (`pipeline/validate`,
  223 stmts, 100%; the in-container G2–G8 bodies are outside `coverage.py`'s
  process-boundary visibility by construction — see §3's note). All four
  of `15` T6.4's accept criteria verified individually against a live
  container, not deferred — these are mechanism claims (does G4 actually
  gate, does G6 actually separate `newly_failing` from `already_failing`),
  not the corpus-wide statistic T6.4a still owns.

### T6.3 — Sandbox isolation, the full `07` §12 checklist

- **Every checklist item became a real, automated assertion against a
  live container** (`test_sandbox_isolation_security.py`) — DNS/TCP
  blocked, `EROFS` on `/etc`, `mount`/`unshare`/`ptrace(ATTACH)` all
  `EPERM` (`ptrace(PTRACE_TRACEME)` against *itself* deliberately not
  tested — that request needs no elevated capability on any Linux system,
  sandboxed or not, so a permissive result from it is not a finding), a
  fork bomb contained by `pids_limit`, an over-limit allocation OOM-killed
  inside the container only, an infinite loop SIGKILLed at the configured
  timeout, no unexpected host path under `/proc/self/mountinfo`.
- **One real, disclosed finding: `GPG_KEY` failed the original name-
  pattern regex** (`(KEY|TOKEN|SECRET|PASSWORD|DSN|URL)`) despite holding
  a public release-signing key fingerprint, not a credential — baked into
  `python:3.12-slim-bookworm`'s own image layer, which Docker's container
  `Env` merges with rather than replaces, so no Dockerfile or Engine API
  mechanism can unset it. Replaced with an explicit allowlist
  (`orchestrator.py`'s `KNOWN_BASE_IMAGE_ENV_KEYS`) plus a direct check
  that none of the worker's actual secret-bearing env var names is
  present — the invariant `07` §3 L7 actually cares about, checked more
  precisely than a name regex ever could.
- **T6.1/T6.2's isolation config is not separable from T6.3's checklist at
  the code level** — every flag was already set on every `create()` call
  from T6.2 onward; T6.3's own contribution is the formal proof, not a
  second hardening pass.

### T6.2 — Sandbox orchestration

- **`SandboxOrchestrator`: create → start → write stdin → wait with a
  hard `SIGKILL` timeout → extract the delimited result → always
  remove**, plus an independent `SandboxReaper` for the case a
  supervising worker process itself died mid-validation. Every `07` §3
  isolation flag is part of the same `_build_create_config` call — there
  is no "orchestrate first, isolate later" sequencing that does not mean
  running untrusted code unconfined in between.
- **Concurrency measured correctly on the second attempt, not the
  first.** An initial instrumentation approach counted "task started" to
  "task fully returned including cleanup," which overshoots the
  semaphore's actual scope and reported 4 concurrent containers against a
  configured limit of 2. Polling the Docker API directly for containers
  in `running` state — what a semaphore protecting *host resources*
  actually needs to bound — confirmed the real number: exactly 2, never
  more, under 8 concurrent runs.
- **`runc` requested when `runsc` (gVisor) is unavailable, rather than
  the create call failing** — this dev host has no gVisor installed; `07`
  §11 already treats this as an accepted, disclosed gap (seccomp +
  AppArmor + network-none still hold).

### T6.1 — The sandbox image

- **`roottrace/sandbox-python:3.12`**: digest-pinned `python:3.12-slim-
  bookworm` base, an offline wheel cache scoped to the V1 fixture
  corpus's own dependencies (not `07`'s "~600 packages" production-scale
  aspiration — `15` T6.1's actual accept bar only needs this corpus,
  offline, reproducibly) plus the pinned analysis toolchain, and the new
  `roottrace_sandbox_runner` package as `ENTRYPOINT`.
- **Two real corrections to `07`'s own B10 mechanism, both found by
  testing against Docker Engine 29.5.3 rather than trusting the doc, and
  both fixed without relaxing any isolation control:**
  - `docker cp`/`put_archive` is rejected outright — `"container rootfs
    is marked read-only"` — against *any* destination on a container
    created with `read_only: true`, regardless of container state or
    which mount the destination resolves to, not just the tmpfs-timing
    problem B10 already described. **Fix:** input travels over the
    container's stdin after `start()` instead — a pipe, not a filesystem
    write, unaffected by either problem.
  - `/work`'s tmpfs content does not survive the container's own process
    exiting, so reading `result.json` back via `cp` after `wait()` finds
    nothing. **Fix:** `runner.py` also emits the result JSON to stdout,
    delimited (`===ROOTTRACE_RESULT_START===`/`_END`), which `docker
    logs` retrieves reliably from a stopped container.
  - **A third finding, smaller but load-bearing:** Docker mounts every
    `Tmpfs` entry `noexec` by default unless the option string says
    otherwise. `07` §3 L2 names `noexec` for `/tmp` but not `/work`, by
    implication meaning `/work` should stay executable — omission alone
    does not override Docker's default, though. Without `exec` added
    explicitly, G2's dependency install succeeded but every installed
    package with a compiled extension then failed to import —"failed to
    map segment from shared object" — which would otherwise have looked
    exactly like a patch problem, not an infrastructure one.
- Both `docs/07-SANDBOX-VALIDATION.md` and this file were updated in the
  same commits as the code, per `CLAUDE.md`'s "documentation drift is a
  defect" rule — nothing here was left as tribal knowledge in a commit
  message.

### T5.4 — Stage 7 `patch`

**Read `06` §5.1's implementation note alongside this section.**

- **Same two-model split as T5.2/T5.3, extended one field further.**
  `contracts.py`'s `Patch` (frozen, `extra="forbid"`) is `03` §S7's literal
  output contract; `extraction_schema.py`'s loose `PatchReply`
  (`extra="ignore"`) is what the gateway's structured-output ladder
  validates the model's raw JSON against. T5.3 established that
  `model`/`prompt_version`/`tokens` never come from the model's own
  JSON — T5.4 extends that rule to every field this ticket's own code can
  independently know: `patch_id`/`base_commit` are caller-supplied (same
  precedent `ranking.build_context_bundle`'s `bundle_id` parameter already
  set — minting identifiers is an orchestration concern), and
  `files_changed`/`scope_warning` are computed deterministically from the
  parsed diff, never trusted from the model. None of the seven have a
  field on `PatchReply` at all.
- **`diffing.py` is the literal `03` §S7 line: "we apply it in-memory with
  `unidiff` before accepting."** `unidiff` parses; it has no built-in
  apply-to-a-string, so that half is this ticket's own code, walking each
  hunk's context/removed/added lines against the actual retrieved content
  rather than trusting the model's line numbers. **A `BundleFile.content`
  is a window, not the whole file** — a hunk is only checkable when its
  full source range falls inside the window that was actually retrieved;
  a hunk reaching outside that window is treated as a failure to apply
  cleanly, not accepted on faith. Two retrieved-content sources, not one:
  `bundle.files` (windowed) and `bundle.tests.found` (whole-file, strategy
  E fetches complete test files) — a diff touching an *existing* test file
  only has content to check against in the second source.
- **`validate.py` builds `03` §S7's full "Constraints enforced on the
  output" table, not a subset.** The allowlist is `fix_strategy.files_to_
  modify` plus the reply's own declared regression-test path (not a
  filename-pattern guess at what counts as "a test file"); forbidden paths
  (`.github/**`, `Dockerfile`, `docker-compose*`, `*.lock`) are checked
  independent of the allowlist, defense-in-depth; `fix_strategy.
  must_not_modify` is checked explicitly too, even though it should
  already be excluded by the allowlist, for the same reason; existing-test
  deletion covers both a whole-file removal and a `def test_*` line
  removed with no same-named replacement in the same hunk. The
  `>60`-line/`>5`-hunk and dependency-manifest checks are warnings
  (`scope_warning`), not failures — `03`'s own wording is "flagged for
  human review," not rejected.
- **Two registered failure codes, not one, and `PatcherUnavailable` carries
  which applies.** `17` GLOSSARY gives S7 `RT-AI-0005` (scope violation
  after retry) and `RT-AI-0006` (diff does not apply after retry) — unlike
  S6's single `RT-AI-0004`, T5.4 needed a place to carry the distinction
  past the exception boundary, so `PatcherUnavailable.error_code` exists
  where `ReasonerUnavailable` needed no equivalent field. A required-but-
  missing regression test is bucketed under `RT-AI-0006` (`03` gives this
  constraint no third code of its own, and "the patch isn't acceptable as
  delivered" is closer in kind to a bad diff than to a scope/injection
  signal); a gateway-level provider exhaustion uses the generic
  `RT-AI-0001`, unrelated to either of S7's own checks.
- **No `PatchOutcome` wrapper, unlike `reason.stage.ReasonOutcome`** — a
  deliberate divergence from T5.3's shape, not an oversight. S6 needed a
  wrapper because it has two real, differently-shaped terminal states
  (`RootCauseAnalysis` or a populated `InsufficientContext`); `04`'s
  `patches` table has no "failed" row at all (every column but two is
  `not null`), so a terminal S7 failure is not a value to return — it is
  `PatcherUnavailable` propagating to the caller. Inventing an
  always-empty failure branch to match T5.3's shape would have been the
  abstraction `CLAUDE.md` says not to add.
- **H4 (Tree-sitter symbol existence) is disclosed as not built, not
  silently skipped.** `06`'s guardrail catalogue names H4 as an S7
  post-validator, but `03` §S7's own constraint table has no row for it
  and neither of `15` T5.4's accept criteria requires it. A heuristic
  AST-based check (reusing `pipeline/retrieve/ast_index.py`) risked
  false-rejecting correct patches referencing legitimate symbols this
  codebase has no full-repo index to verify against, with no accept bar
  actually demanding the check exist. §5 item 18.
- **Explicit accuracy-bar deferral, the same coordinator decision applied
  a second time, not re-litigated.** `15` T5.4's `≥24/25 diffs apply
  cleanly` is a corpus-wide statistical claim over real model output, the
  same shape as T5.3's `≥20/25` bar, resolved the same way: one live-gated
  test (`test_patch_live.py`, 5 real fixture cases, skipped without
  `RT_ANTHROPIC_API_KEY`) running the full S4→S5→S6→S7 chain with a real
  model, enough to catch a catastrophic mechanism failure before Phase 10
  (sandbox) is built on top of this stage, explicitly not the accuracy
  measurement itself. `15` T5.4's own accept-criteria note and §5 item 19
  now say this explicitly.
- **99% coverage on `pipeline/patch`** (360 stmts), clearing `14` §10's
  pipeline-stage floor — checked deliberately, same discipline as every
  ticket since T4.1.

### T5.3 — Stage 6 `reason`

**Read `06` §6.4's implementation note alongside this section.**

- **Two-Pydantic-model split, same pattern as T5.2's `understand`
  contracts.** `contracts.py`'s `RootCauseAnalysis` (frozen, `extra="forbid"`)
  is the trusted, post-validation shape; `extraction_schema.py`'s loose
  `ReasonReply` (`extra="ignore"`) is what the gateway's structured-output
  ladder validates the model's raw JSON against. `ReasonReply` deliberately
  has no `model`/`prompt_version`/`tokens` fields at all — see below.
- **Evidence-binding validator (`validate.py`) implements H1/H2 and S6's
  half of H3 for real**, not schema validation standing in for them, per
  `06` §4.2's S6 semantic-validator row. Two rules, both taken directly from
  `03` §S6's worked example rather than invented: a reasoning step or
  eliminated hypothesis that declares **zero** evidence is valid and kept
  unconditionally (the worked example's own `hypothesise` step has no
  `evidence` key — speculative, not yet grounded, not thereby invalid); a
  step that **declares** evidence and gets **any** of it wrong is dropped
  **entirely**, not partially — a claim partly grounded in a fabrication is
  not a claim worth keeping partially. `fix_strategy.files_to_modify` is
  filtered to paths actually present in `bundle.files`.
- **"The primary root-cause finding"** — the thing `03` §S6 says gates the
  retry-once-then-terminate ladder — is operationalised as: at least one
  surviving `conclude`-type step bound to real evidence, **and**
  `fix_strategy.files_to_modify` ⊆ retrieved paths. Both required; either
  failing triggers the correction retry.
- **A second, S6-specific retry ladder, architecturally distinct from
  T5.1's schema-repair ladder.** T5.1's gateway retries only on JSON-schema
  violation, domain-free. `GatewayReasoner` adds a semantic ladder of its
  own — on evidence-binding failure it re-dispatches the exact same tier
  once with a correction prompt (`_CORRECTION_PREAMBLE`, self-authored;
  `A2` has no literal text for this, only for JSON-malformation repair)
  that quotes the model's own prior reply and states specifically what
  failed to bind. One retry only; a second failure raises
  `ReasonerUnavailable`, added as the third instance of the `XUnavailable`
  N818 per-file-ignore precedent (`TransportUnavailable` →
  `ExtractorUnavailable` → `ReasonerUnavailable`).
- **`insufficient_context` reused as S6's terminal shape too, not a new
  S6-specific type.** `retrieve.bundle.InsufficientContext` already
  describes "not enough to proceed" pipeline-wide (`04`'s status enum has
  one value, not one per stage), and the bundle's own admitted-file/token
  counts stay an honest description of available evidence even when S6
  couldn't validate a conclusion from it.
- **`model`/`prompt_version`/`tokens` on `RootCauseAnalysis` come from the
  real `LLMResult` the gateway returns, never from the model's own JSON
  self-report** — `ReasonReply` has no such fields at all;
  `GatewayReasoner._with_call_metadata()` injects them into the reply dict
  post-hoc, after the gateway call completes.
- **Explicit accuracy-bar scoping decision, made by the coordinator, not
  unilaterally:** `15` T5.3's `≥20/25` and unfixable-01/02-vs-23 accept
  criteria are corpus-wide statistical claims. Neither a full live-eval
  measurement inside T5.3 nor letting `FakeProvider` self-certify the bar
  was acceptable — the first duplicates `T10.1`'s job ahead of time with
  less rigour, the second proves nothing about a real model's capability.
  Resolution: exactly **one** live-gated test, same pattern as T5.1's
  `test_ai_provider_live.py` (skipped without `RT_ANTHROPIC_API_KEY`),
  running `reason()` against **7** real fixture cases (not all 25) with a
  real model — enough to catch anything catastrophically wrong before T5.4
  (patch generation) is built on top of this stage, not an accuracy
  measurement. The formal `≥20/25` claim across all 25 cases, 3 runs, stays
  exactly where `15` already puts it: `T10.1`, Phase 15. `15` T5.3's own
  accept-criteria note and §5 item 14 now say this explicitly, so nobody
  later assumes T5.3 already proved the accuracy number when it only
  proved the mechanism runs.
- **99% coverage on `pipeline/reason`** (344 stmts, 0 miss), clearing `14`
  §10's ≥95% fingerprint/retrieval/scoring-adjacent floor — checked
  deliberately, same discipline as every ticket since T4.1.

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
| 6 | ✅ **Fixture suite verified against stock `python:3.12-slim`, not the hardened image** | T3.1 → T6.1, closed | Re-verified against `roottrace/sandbox-python:3.12` itself once it existed — T6.1's own offline-install accept criterion is exactly this check, and T6.3's isolation suite runs the real image, not the stock one. |
| 7 | **`replay` and `live` transports raise `TransportUnavailable`** | V2 | Deliberate. They are listed and skipped in the contract suite rather than omitted — an omitted transport is one nobody remembers to add. |
| 8 | ✅ **The LLM structured-extraction step of S4 is unimplemented** | T4.1 → T5.2, closed | `GatewayExtractor` (T5.2) is the real `StructuredExtractor` implementation — `UnavailableExtractor` remains as the fallback `03` §S4's own failure-mode table specifies (provider exhaustion, budget exhaustion), not as the only implementation. Tested end-to-end through `understand(...)` against `FakeProvider`. What remains is orchestration, not extraction — see item 15. |
| 9 | **Exception-family accuracy has no margin (23/25, exactly the T4.1 bar)** | T4.1 → T5.2 | `race-01` and `resource-01` are knowable only from breadcrumbs, and the deterministic taxonomy deliberately never reads them (`A1` §9). The extractor at T5.2 is expected to close this; if it does not, the threshold needs revisiting, not the taxonomy. Named explicitly in `tests/integration/test_understand_corpus.py` so a third miss is a build break. |
| 10 | **`POST /v1/repositories/{id}/test_path_mapping` (`05` §6.6) is not wired as an HTTP endpoint** | T4.2 → Phase 16 or first `repositories`-CRUD ticket | `dry_run_path_mapping` is the full resolution logic as a pure function; the route needs `repositories` CRUD, which no ticket through Phase 7 builds. Deliberate scoping decision, not a gap — see `15` T4.2 and `05` §6.6. |
| 11 | **Three corpus cases have no reachable root cause under T4.3's four strategies** | T4.3 → T4.4 / T5.x | `regression-02` (2 hops away), `config-02` (root cause is a composition-root-injected value's producer, no call edge), `type-mismatch-03` (root cause and failure point are sibling functions sharing only data, no call edge). Named by case id in `tests/integration/test_retrieve_strategies_corpus.py::ROOT_CAUSE_UNREACHABLE_BY_T4_3`; whether T4.4's budget allowing a second hop, or S6's reasoning-driven follow-up retrieval, closes any of these is an open question for later phases. |
| 12 | **Release correlation (`03` §S5 strategy D) has no automatic "previous release" lookup** | T4.3 → T8.2 or a `repositories`/releases data source | `strategy_d_git_history`'s `release_diff` is `None` unless the caller supplies `previous_ref` explicitly — the mechanism (`gateway.compare`) is built and tested, but nothing upstream yet knows what the previous release tag was; that needs a releases table or GitHub API call this ticket has no reason to add. |
| 14 | 🔶 **S6's fixable/unfixable split and its `≥20/25` root-cause-file accuracy bar are mechanism-verified at T5.3, not yet measured against real model behaviour** | T5.3 → T10.1 | `GatewayReasoner`'s evidence-binding validator and correction-retry ladder are built and tested against `FakeProvider` — a deliberately fabricated citation is rejected, 100% of surfaced findings pass evidence validation, and one live-gated smoke test (`test_reason_live.py`, 7 real fixture cases) confirms the mechanism runs against a real model without falling over. **Neither of `15` T5.3's two corpus-wide statistical claims — `unfixable-01`/`unfixable-02` terminating as `insufficient_context` while the other 23 proceed, and `≥20/25` fixtures identifying the correct root-cause file — has been measured against real model behaviour across the full corpus.** That measurement is `T10.1`'s job (Phase 15, full 25-case corpus, 3 runs), by explicit coordinator decision recorded in `15` T5.3's accept-criteria note and §4 T5.3 above. Do not close this item until T10.1 runs. |
| 15 | **No orchestration constructs a live `GatewayExtractor`/`LLMGateway`, and no `Settings`-driven factory exists to build one** | T5.1/T5.2 → wherever first runs a real investigation | `apps/worker/roottrace_worker/settings.py` declares every field the gateway needs (API keys, cache TTL, cost caps); `gateway.py`/`GatewayExtractor` take them all as constructor arguments. Every current caller (all of them tests) constructs both directly with explicit providers/routing/storage/db/redis objects and a hand-picked `project_id`. T5.2 proved the *mechanism* works end-to-end against `FakeProvider` — what is still missing is (a) a factory that builds a real `LLMGateway` from `Settings` with real `psycopg`/`redis` connections, and (b) the ARQ task that would construct a `GatewayExtractor` per investigation with real IDs and call `understand(event, extractor=...)` for an actual error. Neither exists yet; both are pipeline orchestration, out of scope for every ticket built so far. |
| 16 | **No provider-health circuit breaker** | T5.1 → undecided | `06` §2.4's responsibilities table names one ("opens ... when a provider exceeds its error threshold"), independent of the B9 cost-cap breaker (built). No section gives it an algorithm the way §8.2a gives the cost breaker one, so nothing was built against a guess. `_dispatch_tier` walks the configured tier order fresh on every call with no memory of a provider's recent failures. |
| 17 | **No provider-side prompt caching** | T5.1/T5.2 → undecided | `06` §2.4/§3.1 name provider-native caching of the static L1-L3 prompt layers (Anthropic `cache_control`, OpenAI automatic caching) as a cost/latency optimisation, distinct from the *deterministic* content-hash cache (built, `ai/cache.py`). T5.2's `assemble_prompt` builds the L1-L5 split *conceptually* (`system`/`domain`/`task` are separate parameters), but the layers are concatenated into one flat `system` string before reaching `RenderedPrompt` — a provider call has no way to mark only the static L1-L3 span cacheable from a single string. Closing this needs `RenderedPrompt`/`ProviderRequest` to carry structured, per-layer content blocks, not just two flat strings; not attempted here since no ticket has scoped that shape change yet. |
| 18 | **H4 (Tree-sitter symbol existence) is not built for S7** | T5.4 → undecided | `06` §5's guardrail catalogue names H4 as an S7 post-validator ("invented function/class names"), but `03` §S7's own "Constraints enforced on the output" table has no row for it, and neither of `15` T5.4's own accept criteria requires it. A heuristic AST-based check (reusing `pipeline/retrieve/ast_index.py`) risked false-rejecting correct patches referencing legitimate stdlib/third-party symbols with no accept bar actually demanding the check exist — built noisily seemed worse than disclosed and deferred. H5 (diff applicability) and H6 (scope enforcement), the other two S7 guardrails `06` names, are both built and tested. |
| 19 | 🔶 **S7's `≥24/25 diffs apply cleanly` accuracy bar is mechanism-verified at T5.4, not yet measured against real model behaviour** | T5.4 → T10.1 | `GatewayPatcher`'s in-memory diff-apply check and scope validator are built and tested against `FakeProvider` — every constraint-table row is exercised, both registered failure codes fire and retry correctly, and one live-gated smoke test (`test_patch_live.py`, 5 real fixture cases through the full S4→S5→S6→S7 chain) confirms the mechanism runs against a real model without falling over. **`15` T5.4's `≥24/25 diffs apply cleanly` corpus-wide statistical claim has not been measured against real model behaviour across the full corpus.** That measurement is `T10.1`'s job (Phase 15, full 25-case corpus, 3 runs), by the same coordinator decision recorded for item 14/T5.3, applied consistently here. Do not close this item until T10.1 runs. |
| 20 | 🔶 **Sandbox p95 (`07`'s own budget) is mechanism-verified per-gate at T6.4, not yet measured across the full corpus** | T6.4 → T6.4a | Every gate's timeout and pass/fail behaviour is proved against real containers (`test_sandbox_gates_integration.py`, 11 cases against live Docker). What is not yet measured is wall-clock p95 for a full `_run_gates()` pass across all 25 corpus fixtures × 3 runs — the same corpus-wide-statistic shape as items 14/19, owned by `T6.4a`, not yet run. Do not close until that measurement exists with real numbers. |
| 21 | **G6's flaky-test handling is not built** | T6.4 → undecided | `03` §S8 does not specify a re-run-on-failure policy for G6 (existing test suite, pre/post), and `15` T6.4's accept criteria don't require one. `gate_existing_tests` runs each suite once per side and classifies by name-set diff (`newly_failing` vs `already_failing`); a genuinely flaky pre-existing test would show up as `newly_failing` on an unlucky run with no retry to distinguish it from a real regression. No corpus fixture currently exercises this, so it is disclosed rather than guessed at. |
| 22 | **AppArmor profile is declared in config (T6.1) but not loaded on this dev host** | T6.1/T6.3 → production hardening | `settings.py`'s `sandbox_apparmor_profile` field exists and the boot invariant requires it set (+ `runsc`) in `production`; T6.3's isolation suite ran with seccomp + `network:none` + `runc` (no gVisor, no AppArmor profile loaded) on this Windows/WSL2 dev host, which `07` §11 already treats as an accepted, disclosed local-dev gap, not a silent one. |

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
§5). Phase 8 and Phase 9 are both mechanism-complete: T5.1 through T5.4 are
all done; both phases' accuracy bars are explicitly deferred to `T10.1`
(§5 items 14 and 19). Phase 10 is mechanism-complete through G0–G8: T6.1
through T6.4 are all done, verified against a real Docker daemon. T6.5
(`15` §8, degraded mode) is next; T6.4a (real sandbox p95 across the full
corpus, §5 item 20) remains open alongside it.**

| Ticket | Scope | Status |
|---|---|---|
| T4.1 | Stage 4 — `understand` | ✅ Done — `apps/worker/roottrace_worker/pipeline/understand/` |
| T4.2 | Frame path resolution | ✅ Done — `apps/worker/roottrace_worker/pipeline/retrieve/path_resolution.py`; corpus at 25/25 |
| T4.3 | Stage 5 — retrieval strategies A, B, D, E | ✅ Done — `apps/worker/roottrace_worker/pipeline/retrieve/strategies.py` |
| T4.4 | Ranking, budget, and quality scoring | ✅ Done — all bars clean; threshold revised per §5 item 13 |
| T5.1 | LLM gateway | ✅ Done — `apps/worker/roottrace_worker/ai/*.py`; all 3 accept bars clean; see §4's T5.1 section and §5 items 16–17 for disclosed gaps |
| T5.2 | Prompt system | ✅ Done — `apps/worker/roottrace_worker/ai/prompts/*`; both accept bars clean; closed §5 item 8; see §4's T5.2 section for the two T5.1 bugs it found and fixed |
| T5.3 | Stage 6 — `reason` | 🔶 Mechanism done — `apps/worker/roottrace_worker/pipeline/reason/*`; 99% coverage; **corpus-wide accuracy bar (`≥20/25`, unfixable split) explicitly deferred to `T10.1`, not verified here — see §4's T5.3 section and §5 item 14** |
| T5.4 | Stage 7 — `patch` | 🔶 Mechanism done — `apps/worker/roottrace_worker/pipeline/patch/*`; 99% coverage; two of three accept criteria (forbidden-path, regression-test) verified deterministically; **`≥24/25` diffs-apply-cleanly bar explicitly deferred to `T10.1`, not verified here — see §4's T5.4 section and §5 item 19** |
| T6.1 | Sandbox container image | ✅ Done — `apps/sandbox-runner/`; builds reproducibly, offline install verified; two B10 corrections + the tmpfs-`exec` finding, see §4's T6.1 section |
| T6.2 | Sandbox orchestration | ✅ Done — `apps/worker/roottrace_worker/pipeline/validate/orchestrator.py`; concurrency bound verified at exactly 2 against a live daemon after a self-caught measurement-methodology fix, see §4's T6.2 section |
| T6.3 | Sandbox isolation | ✅ Done — full `07` §12 checklist automated against a live container (`test_sandbox_isolation_security.py`); one disclosed finding (`GPG_KEY`), see §4's T6.3 section |
| T6.4 | The nine gates | ✅ Done — `apps/sandbox-runner/roottrace_sandbox_runner/gates.py` + `apps/worker/.../pipeline/validate/gates.py`; G0–G8 all real, fail-fast; G4 explicitly confirmed to fail on both a theatrical test and an unrelated-error test and pass only on a genuine fix, formal pytest (`test_sandbox_gates_integration.py`, 11/11 against live Docker) matching manual verification exactly; see §4's T6.4 section. Corpus-wide p95 measurement still open — §5 item 20 |

**T6.5 — Degraded mode** (`15` §8, next, Phase 10, final ticket): read `07`
§5 ("Degraded mode") before starting. Cache-miss handling with honest mode
reporting — cache coverage determines `mode: full | partial | syntax_only`,
gates that need a missing wheel are skipped rather than faked, and the
validation confidence component is capped, never silently left at full
confidence with fewer gates actually run. Accept (`15`): removing a
required wheel produces `mode: "partial"`, the affected gates report
`skipped`, and the confidence component is capped — never a silent pass.
This is the last ticket of Phase 10; P1 ("nothing reaches a human without
proof") is fully satisfied only once a `partial`/`syntax_only` run cannot
be mistaken for a `full` one anywhere downstream.

**Standing rules that still apply:** finish each ticket's acceptance
criteria before starting the next; commit and push after each ticket
individually with CI green; update the relevant doc in the same commit as
the code it describes; a bug fix ships with a test that fails before the fix.
