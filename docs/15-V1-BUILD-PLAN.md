# 15 — V1 Build Plan

> **Build this first.** Ten weeks, ticket by ticket, with acceptance criteria per step. V1 proves the pipeline on fake data. Nothing else.

---

## 1. The V1 mandate

> **V1 runs the complete 14-stage pipeline end to end, entirely on synthetic data, with zero real customer repositories connected.**

### Why fake data first

| Reason | Detail |
|---|---|
| **Ground truth exists** | We know the right answer for every fixture case, so we can actually measure accuracy instead of guessing at it |
| **Zero risk** | No customer repo can be damaged by a pipeline that is still being debugged |
| **Deterministic and offline** | The full pipeline runs with no external dependency except LLM providers. Tests are fast and reproducible |
| **Isolates the real risk** | The hard part is retrieval and validation logic, not GitHub plumbing. Fake data lets us fix the hard part without the plumbing in the way |
| **It ships faster** | No OAuth flow, no App review, no rate-limit debugging in week 2 |

A V1 that runs 14 stages flawlessly on fake data is worth more than a V1 that half-works on real data, because every subsequent phase assumes the pipeline is correct.

### In scope

- All 14 pipeline stages, fully implemented
- Supabase schema, RLS, auth (GitHub OAuth login works — repo *access* does not)
- Ingest API + Python SDK
- The complete dashboard, all pages
- In-app sandbox with all 9 gates
- GitHub client fully implemented, running in `fixture` mode
- 25-case evaluation harness
- Observability and cost tracking

### Explicitly out of scope

Live GitHub repos · repo indexing / embeddings (schema exists, unpopulated) · AI chat · multi-model consensus · auto-merge · billing · team invites · Sentry/Datadog adapters · Node SDK · dark mode

---

## 2. Implementation order — CANONICAL

**Phases, not weeks.** A phase completes when its acceptance criteria pass, not when a calendar week ends. The weeks below are estimates; the *order* is binding.

```
PHASE 0   Specification repair        ← COMPLETE (see `18` §9)
   ↓
PHASE 1   Repository / tooling        T1.1              W1
   ↓
PHASE 2   Supabase schema + RLS       T1.2, T1.3        W1
   ↓
PHASE 3   Auth                        T1.4              W1
   ↓
PHASE 4   FastAPI foundation          T1.5              W1
   ↓
PHASE 5   Fixture system              T3.1–T3.3         W3
   ↓
PHASE 6   Ingestion / fingerprinting  T2.1–T2.5         W2
   ↓
PHASE 7   Retrieval                   T4.1–T4.4         W4  ← the hard one
   ↓
PHASE 8   AI reasoning                T5.1–T5.3         W5
   ↓
PHASE 9   Patch generation            T5.4              W5
   ↓
PHASE 10  Sandbox validation          T6.1–T6.5         W6
   ↓
PHASE 11  Repair loop                 T7.1              W7
   ↓
PHASE 12  Independent review          T7.2              W7
   ↓
PHASE 13  Confidence engine           T7.3              W7
   ↓
PHASE 14  Fixture GitHub transport    T8.1              W8
   ↓
PHASE 15  Evaluation harness          T10.1             W10
   ↓
PHASE 16  Dashboard                   T8.2–T8.4, T9.*   W8–W9
```

**Do not skip ahead because a later component looks easier.** Two orderings differ from the original week numbering and both are deliberate:

- **Phase 5 (fixtures) precedes Phase 6 (ingestion).** You cannot test an ingest path without known-good input. Building fingerprinting first means validating it against hand-written payloads, which are subtly unrealistic in exactly the ways that make the pipeline look better than it is (`A1` §9).
- **Phase 16 (dashboard) is last.** The pipeline viewer was originally built alongside the orchestrator to catch contract gaps early. That benefit is real but smaller than the cost of building UI against contracts that are still moving. The WebSocket contract (`03` §7) is frozen from Phase 4, so the viewer can be built against it whenever.

Sequencing rules that survive from `15` §14 unchanged: schema and RLS before application code; retrieval before reasoning; sandbox before the repair loop; scoring before publishing.

**The rule that matters most:** do not advance past **Phase 7** until retrieval is genuinely good on all 25 fixtures. Everything downstream inherits its errors, and a confident wrong answer built on wrong context passes every later gate.

---

## 3. Week 1 — Foundation

### T1.1 Repository and tooling

```
roottrace/
├─ apps/{api,worker,web,sandbox-runner}/
├─ packages/{shared-types,sdk-python}/
├─ infra/{terraform,docker,supabase}/
├─ fixtures/
└─ docs/
```

- `uv` workspace for Python, `pnpm` workspace for TS
- `ruff` + `mypy --strict` + `eslint` + `tsc --noEmit`
- Pre-commit: format, lint, `gitleaks`
- Makefile as the canonical developer interface — targets defined in `A3` §5.2
- GitHub Actions: lint → unit → integration, **invoking the same Make targets**
- Coverage configured but not enforced this phase (`A3` §6.1 ratchet)

> **`apps/web` in Phase 1 (A2).** Create only the minimum workspace placeholder needed for `pnpm` to resolve — a private `package.json` and a README. **Do not scaffold Next.js yet.** The real dashboard begins in Phase 16; a scaffold created here would sit untouched for the entire build and its dependency tree would be stale before first use. Consequence, stated rather than hidden: `eslint` and `tsc --noEmit` have nothing to check in Phase 1, so the TS half of `make check` is a no-op until Phase 16.

**Accept:** `make check` passes on a clean clone. CI green on an empty PR. `make check` and the CI check job run the identical target set.

### T1.2 Supabase local + schema

Write all **15** migrations from `04-DATA-MODEL.md` §15, in order. `…000900_auth_helpers.sql` **must** precede `…001000_rls_policies.sql` (every policy references `rt_auth.*`), and `…001500_rls_assertions.sql` **must** run last.

**Accept:**
- `supabase db reset` applies every migration cleanly from an empty database.
- `supabase db diff` is empty.
- All **26** logical tenant tables have RLS enabled **and forced**, verified by the §12.9 coverage assertion, which fails the *migration* rather than a test.
- The policy-presence assertion passes — no relation is RLS-enabled with zero policies.
- The `rt_auth` helpers exist, are **not** `SECURITY DEFINER`, and have pinned `search_path`. No role in the database holds `BYPASSRLS` (ADR-009).
- **Every partition carries its own forced RLS and its own policies (B13).** A test queries `raw_events_2026_08` and `error_occurrences_2026_08` **directly** as `authenticated` from project A and asserts **zero rows** from project B.
- **The maintenance job cannot reopen the gap.** Running `rt_admin.ensure_partitions()` produces partitions that are already secured; a partition created without `secure_partition()` fails the assertion migration.
- **The integration CI job exists, is uncommented, and runs on every PR.** T1.1 left it commented out in `.github/workflows/ci.yml` because Phase 1 has no integration suite and `pytest -m integration` exits 5 on an empty selection. The alternative — a flag that tolerates an empty run — was rejected as a permanent escape hatch for a two-week problem. This bullet is what makes it self-correcting rather than remembered.

### T1.3 RLS and membership security suite

The parameterised cross-tenant test from `14` §4.1, **plus** the membership escalation suite from `14` §4.1a, the aggregate isolation suite from §4.1b, and the audit visibility suite from §4.1c.

**Accept:**
- 26 tables × 2 tests (read + write) pass.
- Deliberately dropping a policy makes the corresponding test fail — the suite has teeth.
- All 12 membership tests pass, including the four architecture regression guards (`test_no_policy_references_own_table`, `test_all_definer_functions_pin_search_path`, `test_anon_cannot_execute_rt_auth`, `test_no_helper_takes_user_id`).
- Matviews are unreadable directly; their accessors return empty cross-tenant.
- Org-scoped audit events are visible to org owners and to nobody else.
- **Coverage on auth/RLS code ≥ 95%, enforced from this phase** (`A3` §6.1).

### T1.4 Auth

Supabase GoTrue. Login, callback, session, logout. **Asymmetric JWKS** verification middleware in `api` (B12) — the algorithm is read from the matching JWKS entry, never from the token header and never hard-coded. GoTrue signs ES256 in the deployed build; see the B12 addendum in `A3` §1.

Local authentication follows `A3` §5.1: GitHub OAuth when `RT_GITHUB_CLIENT_ID`/`SECRET` are configured, Supabase magic link otherwise. Both paths issue the same JWT with the same `sub`, so `auth.uid()` and every RLS policy behave identically — **there is no dev-mode auth bypass.**

Refresh rotation and reuse detection are **GoTrue's responsibility**. We configure and verify them; we do not build a token store.

**Accept:**
- Sign in (either path) → JWT issued → `/v1/me` returns the user → RLS scopes correctly.
- A token signed with the wrong key is rejected; a `kid` miss triggers exactly one JWKS refetch.
- Refresh tokens rotate — **verified against GoTrue**, not reimplemented.
- **OPEN: reuse detection does not work and is not verified.** Replaying a consumed refresh token returns 200 with `GOTRUE_SECURITY_REFRESH_TOKEN_REUSE_INTERVAL` at both 10 and 0 (checked inside the container), which suggests 0 means *unlimited* rather than *none* in this build — so setting 0 would weaken the control while appearing to tighten it. Until resolved, a stolen refresh token is replayable and `11` T15's mitigation is incomplete. Do not close T1.4 as fully done on this criterion.
- `api` boots with `RT_SUPABASE_JWKS_URL` and refuses to boot if handed a service-role key or a legacy `RT_SUPABASE_JWT_SECRET`.
- Missing GitHub OAuth credentials do not block any of the above.

### T1.5 API skeleton

FastAPI app, typed settings, structured logging with redaction, `request_id` middleware, error handler emitting the standard envelope, `/health` and `/health/ready`.

**Accept:** Container builds and runs as non-root. Health checks respond. A deliberate exception produces the standard error envelope with `request_id`, and the log line contains no secrets.

**Done.** All criteria pass, re-proved on every commit by `tests/integration/test_container.py` (the image is built by the suite, not assumed present) and the unit suites for the envelope and redaction. The coverage ratchet moves to **60** here, per `A3` §6.1.

Six defects found and fixed while building it, five of them controls that looked applied and were not:

- **The catch-all handler was outside its own middleware.** FastAPI routes a registered `Exception` handler to Starlette's `ServerErrorMiddleware`, which wraps every user middleware — so a 500 arrived with no `X-Request-ID`, no security headers, and a null `request_id` in its own envelope, because the contextvar had already been reset. The catch-all now lives in `RequestContextMiddleware`.
- **405 escaped the envelope entirely**, since no registered code mapped to it. `RT-VALIDATION-0002` added to `17` §4 rather than reusing an unrelated code.
- **The container image had no dependencies.** `uv export --no-dev` against the workspace root exports nothing (every member is in the `dev` group), the install of an empty requirements file succeeded, and the image built green and died at startup. See `13` §3.
- **The container's boot log lines were plain text.** uvicorn logs before it builds the app, so with `uvicorn --factory` as the entrypoint those lines escaped the processor chain. `serve.py` is the entrypoint now.
- **`--no-access-log` was being undone by our own `configure_logging`**, which re-enabled propagation on the logger uvicorn had just switched off — a duplicate, unredacted access line per request.
- **Coverage measured only imported modules.** `source = ["apps", "packages"]` looked right, but coverage recurses into a source directory only when it contains `__init__.py`, and `apps/` does not — so a module with no tests at all was absent from the report rather than 0%, and the overall floor could be satisfied by not importing something. Caught because `serve.py` failed to appear. Fixed to name the package directories.

**Deferred deliberately:** CORS (SC63). It needs a dashboard origin that does not exist until T8.2 and a new `RT_*` variable to hold it. With no CORS middleware the browser default is that no cross-origin page can read a response, which is the safe direction to be wrong in; a permissive placeholder is not.

---

## 4. Week 2 — Ingest

### T2.1 `POST /v1/events`

Auth, rate limit, idempotency, per-event schema validation with partial success, sanitisation, batch insert, blob write, enqueue.

**Accept:** 100-event batch persists in < 50 ms p95. Two invalid events are rejected individually while 98 succeed. Replaying the same `Idempotency-Key` returns the original response without re-inserting.

**Done except the p95 criterion, which is open and measured below.** 18 end-to-end tests against real Postgres and real Redis — nothing on this path is stubbed, because idempotency and rate limiting are concurrency properties and a fake proves only that the test agrees with itself.

**The spec contradicted itself about the write path, and `rt_ingest` is the resolution.** S1 runs in `api`; `api` must not hold the service-role key; `raw_events` has forced RLS with no INSERT grant to `authenticated` — and ingest has no user JWT at all. A dedicated role with **no BYPASSRLS and no password**, reached by `SET LOCAL ROLE`, scoped by a `WITH CHECK` against `rt.project_id`. The database refuses a cross-tenant write even when the handler asks for one.

Scoping that role also required moving `tenant_read`/`tenant_write` on the three tables ingest touches to `TO authenticated`. The alternative — granting a **write-only** role read access to every project's membership so it could satisfy a policy that can only ever return false for it — was rejected.

**`GITHUB_MODE`-style parity for the queue:** step 9 enqueues to `rt:ingest` in Redis. ARQ consumes it in W2; the contract between them is the queue name and the payload shape.

**Open, deliberately:**

- **Object storage (step 8) is not implemented.** `payload_url` is null. The api holds no credential that can write to Supabase Storage — by the same boot invariant that keeps the service-role key out of it — so the archive write belongs to the worker, which has one. Deferred rather than worked around with a second credential in the most internet-exposed service.
- **Sanitisation (step 6) is T2.2.** The seam is in place and every accepted payload passes through it, but `sanitise` returns the payload unchanged and says so. A handful of patterns here would report `redactions: []` for a payload full of secrets, which is the most misleading output this step could produce.

- **The p95 budget is NOT met, and this criterion is open.** Linux CI measures **median 28 ms, p95 226 ms**. The samples are bimodal: roughly fifteen at 26–33 ms and five at 94–230 ms. The median says the work itself fits the budget with room to spare; the tail is an *unidentified periodic stall*, not a constant overhead, which makes it a real defect rather than a platform cost. The test is `xfail(strict=False)` with the numbers in its reason, so the failure stays visible and the day it starts passing is visible too. Widening the threshold to make it green was rejected — that would delete the only signal we have.

  Pipelining the two scoping statements with the insert did help: it took the *Windows* p95 from 102 ms to 74 ms. Windows also carries a constant this code cannot remove (every packet crosses Docker Desktop's NAT; ~48 ms median for the insert alone), so the local ceiling is 150 ms — enough to catch a regression that doubles the cost, while CI holds the real number.

### T2.2 Sanitisation

Every pattern from `03` §S1: AWS keys, GitHub tokens, JWTs, private keys, provider keys, high-entropy strings, emails, Luhn-valid card numbers, header allowlist, field caps.

**Accept:** A payload seeded with one of each pattern emerges fully redacted, with `redactions` recording `{path, kind}` and never the value. No false positives on 500 lines of ordinary source code.

**Done.** 36 tests. Every pattern from `03` §S1 — AWS, GitHub (classic and fine-grained), provider keys, Slack, JWTs, private keys, DSN credentials, our own ingest keys, Supabase keys, emails, Luhn-valid cards, high entropy — plus the header allowlist. Wired into `POST /v1/events` before anything is persisted, with the redactions stored on the row.

Three decisions worth recording:

- **Headers are dropped, not redacted.** An allowlist cannot be defeated by a header nobody thought of; a denylist can. `Authorization` and `Cookie` therefore never reach storage in any form.
- **Cards are confirmed by Luhn, not by shape.** A 16-digit order number is not a card, and redacting one would remove the identifier an engineer needs to find the failing request.
- **Entropy applies only to undelimited tokens.** `03` §S1 says "in a value position"; in practice a stack trace sits near the 4.5 threshold across its whole length, and `repr` escapes newlines so a CSV excerpt in a local variable looks like one long high-entropy token. Both restrictions are deliberate false-negative trades, stated in the code: a credential containing a comma slips past *this* rule, and the named patterns — which do not depend on it — are what catch every format we know.

The false-positive criterion is tested against the **synthetic repository**, ~1,800 lines, rather than a hand-picked sample: code chosen for the test would be code already known to be safe. Running it over our own corpus found a real false positive (`resource-01`'s CSV variable) and a double-redaction bug where a cleaned DSN was re-flagged as high entropy — both fixed, both now covered.

This module is deliberately **stricter than log redaction** (`11` §8.3). That one runs over our own log lines where a false positive destroys an operator's evidence; this one runs over customer payloads where a false negative persists a secret forever. Same project, opposite tolerance for error.

### T2.3 Fingerprinting

`normalize_message`, `top_in_app_frames`, `compute_fingerprint`, custom rules, atomic upsert.

**Accept:** All parametrised cases from `14` §3 pass. 100 concurrent identical inserts produce exactly one issue with `occurrence_count = 100`.

**Done.** 40 tests: `14` §3's four parametrised cases verbatim, every `normalize_message` rule, `top_in_app_frames`, custom rules, and the upsert raced against real Postgres.

**The concurrency criterion is run as a real race**, not as a code path — 100 concurrent upserts of one fingerprint produce one issue with `occurrence_count = 100`, and exactly one of the hundred reports `is_new_issue`. A read-then-write formulation passes every sequential test and fails this one; `(xmax = 0)` is how the statement reports which branch it took, and a second query to find out would reopen the race the upsert exists to close.

Both directions are tested throughout. A fingerprint function returning a constant would pass every "these group together" case on its own, so each is paired with a "these must not" — different function in the same file, different exception type, different route, different project.

**The 25 corpus fingerprints are now populated**, computed by `compute_fingerprint` itself, and `test_fingerprints_are_left_for_the_implementation` inverts exactly as T3.2 said it would. They are asserted to be distinct: a collision would merge two cases in the evaluation harness and every metric computed over them would silently describe the wrong pair.

Two properties beyond the stated criteria, both about how storms actually behave: a late occurrence cannot rewind `last_seen` (a buffered SDK flushing after a partition would make an active issue look dormant), and a resolved issue that recurs becomes `regressed` rather than merely `open` — the difference between "known" and "we thought we fixed this".

### T2.4 Triage

Severity scoring, investigation gating, cooldown, quota check.

**Accept:** Score matches hand-computed values at every band boundary. Each of the six gate reasons is individually reachable and correctly reported.

**Done.** 34 tests.

**The arithmetic is computed by hand in the tests**, not by calling the implementation and asserting it equals itself. Every weight at full sums to exactly 1.0 — which is what makes the score a fraction rather than an arbitrary number, and the bands comparable across projects — and `03` §S3's own worked example (0.24 / 0.18 / 0.20 / 0.15 / 0.02 → 0.79) is reproduced by working backwards to the inputs that produce it.

**Every band boundary is asserted at its exact value and one ten-thousandth below.** An off-by-one comparison passes a sampled test and fails here.

**All six gate reasons are reached one at a time**, each by changing exactly one input from a baseline that would otherwise investigate — with a positive control that the baseline *does* investigate, since a gate that always refused would satisfy every "is gated" assertion on its own. A test also pins the enum to exactly six members, so a seventh reason cannot appear untested.

Two orderings are deliberate and asserted: `already_investigating` is reported before anything else (it is the outcome B8 makes indistinguishable from being gated), and `muted` before `below_min_severity` — a user set the mute deliberately and that is the reason they would look for, not a threshold they never touched.

`endpoint_criticality` resolves the **longest** matching glob, so a broad pattern added later cannot quietly downgrade a specific one already there.

**Not yet wired to the database.** The B8 insert-and-handle-conflict path needs the `investigations` table and the partial unique index, which belong to the orchestrator (T8.2). `evaluate_gate` takes `has_active_investigation` as an input for exactly that reason: the gate is advisory, and the caller supplies what the database told it.

### T2.5 Python SDK

`init`, `capture_exception`, `add_breadcrumb`, FastAPI middleware, batching, retry, local buffer, never-raises guarantee.

**Accept:** A demo FastAPI app throws an exception → the event arrives with parsed frames and breadcrumbs. Killing the API mid-run causes buffering, not a crash in the host app.

**Done.** 171 unit tests plus 13 integration tests. **Both acceptance criteria are proved against real sockets** (`tests/integration/test_sdk_end_to_end.py`): a real FastAPI app under a real ASGI stack posting to a real HTTP listener, which is then genuinely killed and restarted. Neither half survives a fake transport — a stub returning "unreachable" tests our handling of a value we invented, not our handling of a closed socket, and `route_pattern` only exists because Starlette put it in the scope.

The buffering half asserts three things, because only asserting the first would pass for an SDK that discards: the application still answers every request while the API is down; the events are **buffered**, with `dropped == 0` and nothing reaching an API that is not running; and all six arrive, de-duplicated by `event_id`, once it comes back.

**The never-raises guarantee is tested by breaking seams, not by hoping.** "It did not raise" is satisfied equally well by code that never ran, so `_guard` reports every swallowed exception to a sink and each test asserts both halves — the documented default came back *and* the guard is what caught it. A positive control asserts the same call succeeds untouched. `KeyboardInterrupt` and `SystemExit` are asserted to still propagate: the guard catches `Exception`, not `BaseException`, because swallowing a `CancelledError` inside a middleware turns a cancelled request into a hung one.

**The middleware re-raises**, and that is the one place the guarantee deliberately does not apply.

Decisions worth recording:

- **Zero dependencies**, so the transport is `urllib.request` and the middleware is raw ASGI with no `fastapi`/`starlette` import. The cost is three things duplicated from `apps/api` — UUIDv7, the header allowlist, and the payload shape — and `tests/integration/test_sdk_contract_agreement.py` is the only place both packages are imported together, failing if any of the three drifts. It also asserts the SDK's payload passes the server's **own** `validate_batch` and `sanitise`, rather than a shape asserted inside the SDK's tests.
- **The event buffer drops the newest on overflow; the breadcrumb trail drops the oldest.** Opposite ends, opposite reasons: a buffer fills during an incident where the events are repetitions and the first ones carry the origin, while the breadcrumb contract is literally "the last N before the error".
- **Breadcrumbs live in a `ContextVar`, and the middleware `set`s a new deque per request** rather than clearing the old one. A shared list interleaves concurrent requests and produces a report that names another request's database call — confidently wrong, which is worse than absent. Tested with an `asyncio.Barrier` so the interleaving is forced rather than hoped for.
- **The sender thread starts lazily, on the first event, not in `init`.** `init` runs at import time and a pre-fork server forks after that; threads do not survive `fork`, so the child would buffer silently and drop everything once full. `os.register_at_fork` resets what a child inherits, and a pid check covers the platforms without it.
- **Retries reuse the batch's idempotency key** (B7). A fresh key on the retry of a timed-out-but-persisted batch duplicates it and can buy a second paid pipeline run.
- **A 4xx is dropped, not buffered.** Leaving a permanently-rejected batch at the head of the buffer blocks every event behind it forever — a revoked key would silence the SDK permanently rather than for as long as the key is revoked.
- **`init` refuses a malformed `api_key` and says so on stderr.** It would otherwise produce 401s, which the transport correctly refuses to retry, and the developer would see an application with no errors — the failure mode with the longest time-to-discovery.
- **The endpoint must be HTTPS, or loopback.** The key travels in an `Authorization` header on every request.
- **`traceback.TracebackException`, not `StackSummary.extract(walk_tb(...))`.** The obvious spelling silently returns `colno = None` on every frame; the column test asserts against the raw source line rather than `>= 1`, which the broken version would also satisfy.

Three deviations from the spec, all recorded in `05` §10 in the same commit:

- **Import name.** §10 said `import roottrace`; the package is `roottrace_sdk`, matching the distribution `roottrace-sdk`. A distribution and import name that differ is the `beautifulsoup4`/`bs4` papercut and there is no reason to inherit it.
- **`before_send` receives a `dict`,** not an object with `e.error.type`. The attribute form is not implementable in a package whose dependency set must stay empty.
- **Local variables are off by default.** `03` §S1 shows `vars` marked "// redacted", but redaction happens at ingest — by which point a password in a plain local has already left the customer's process, and neither the entropy rule nor the pattern list catches `hunter2`. `capture_locals=True` opts in, with client-side redaction of secret-shaped names.

**Two things are not implemented and are visible rather than silently absent.** The middleware does not capture a handler that catches its own error and returns a 500 itself: there is no exception object, and an event without `error.type` is rejected by `RT-INGEST-0011` — guessing a type from a status code would group every unrelated 500 in the service into one issue. And `request.body_sample` is never read by the middleware, because doing so means draining and replaying `receive` for every request whether or not it fails; it can be passed explicitly to `capture_exception`.

**A gap in the secret scan was found and closed on the way.** `.gitleaks.toml` had a rule for `rt_live_` and none for `rt_test_`, although `05` §2.1 gives the format as `rt_{live|test}_{32 hex}` and a test-mode key is a real credential for a real project. Half a format is the shape of control this project treats most seriously: it reports clean on the half it does not read. Noticed because this ticket put key-shaped strings into a dozen test files for the first time. `test_every_api_key_prefix_the_spec_defines_has_a_gitleaks_rule` is the guard, and it was verified by disabling the new rule and watching it fail. Nothing is allowlisted against it — the suite builds its fake keys by concatenation, so no key-shaped literal exists to excuse, and an allowlist entry would be the fail-open version of the rule.

`mypy`'s "Duplicate module named conftest" forced one configuration change: pytest resolves a conftest per directory, mypy resolves by module name, and a second `conftest.py` anywhere stops the run before it checks anything. None of mypy's suggested fixes apply — `__init__.py` still yields two `tests.conftest`, and `packages/sdk-python` is not a legal identifier. `conftest.py` is excluded from mypy; every test module that uses those fixtures is still checked.

**Coverage ratchet raised 60 → 75** (§6.1's Phase 6 floor). Actual is 88%.

---

## 5. Week 3 — Fixtures

### T3.1 Synthetic repository

~40 files. Realistic layered Python service: `api/routes`, `services`, `clients`, `models`, `tests`. Real dependencies in `requirements.txt`. An existing, deliberately imperfect test suite. Simulated git history in `.roottrace-fixture.json` with blame data, commits, and release tags — including the specific commits that introduce the fixture bugs.

**Accept:** The repo installs and its test suite runs green inside the sandbox image. Every one of the 25 bugs is genuinely present in the code.

**Done.** Verified by `tests/integration/test_fixture_repo.py` (63 tests) and `make fixtures-verify`, which runs in CI so a refactor of the fixtures cannot silently invalidate the evaluation harness.

Four contradictions in the spec had to be resolved to build it:

- **`A1` §5 required `tests/test_quote.py::test_estimate_with_missing_tax`** for `regression-02`, but §2's tree had no such file — and its six test files summed to exactly the stated 49 tests, so the file had been dropped rather than the count. Added; `A1` §2 and `18` §7 corrected to 42 files, 52 tests, 50 passing.
- **"Runs green" vs "two tests fail before any patch."** Both are true only under one reading: the suite runs to completion at a *known baseline*. Asserting zero failures would delete the two that exist to exercise G6's `already_failing` branch; asserting "two failed" would accept any two. The test names both and asserts they are unrelated to any case, because a baseline failure tied to a case would flip to passing when that case is fixed and corrupt G6's accounting.
- **"Inside the sandbox image"** — that image is T6.1, eight phases away. Checked against the same pinned `python:3.12-slim` base with **`--network none`**, which is the property T6.1 would otherwise inherit as a surprise: a fixture suite that quietly needed the internet would pass locally and fail in the sandbox for reasons that look like a patch defect. Re-verify against the hardened image at T6.1.
- **`18` §7 pins the canonical defect to lines 38–43; `A1` §4's inline comment said line 41.** The registry wins. The code is written to it and the line numbers are asserted individually, since every document quotes them and the evaluator compares the model's citation literally.

The line total is **~1,780, not the ~2,400 estimated**. Reported rather than padded — 39 modules across seven layers is what makes retrieval cross real boundaries, and filler would make the corpus look harder than it is.

**`fixtures/triggers/` is the mechanism for the second criterion.** `A1` §9 says a bug you cannot trigger by running the code is a fiction, so each case has a reproduction that executes the repository. Two of them found real defects in the corpus while being written: `boundary-01` was not a bug at all (with 1-based pagination, `offset - 1` is correct), and both controls were leaking raw transport exceptions — meaning the handling was *not* already correct and they would have been fixable cases rather than controls. `race-01` needed `sys.setswitchinterval` to open the window, which is also why it survived review in the story and why the single-threaded suite is green. T3.2 captures its payloads from these tracebacks rather than hand-writing them.

### T3.2 Error corpus

25 error payloads matching the distribution in `14` §6.1, each with a ground-truth file.

**Accept:** Every payload validates against the ingest schema. Every ground truth references real symbols at real line numbers in the synthetic repo.

**Done.** 25 payloads and 25 `.case.json` ground-truth files, verified by `tests/integration/test_fixture_corpus.py` (324 checks) and wired into `make fixtures-verify`, which runs in CI. Line ranges are resolved from the **AST** of the code they name, so a ground truth cannot drift from the repository without a failing test — drift here does not fail loudly, it quietly changes what the harness measures.

Three decisions worth recording:

- **The payloads are generated from real tracebacks, not written.** `fixtures/corpus/generate.py` runs each trigger and walks the captured `__traceback__`. `A1` §9's rule against hand-written traces is enforced by construction rather than by discipline, and regeneration is deterministic so a fixture refactor produces a diff rather than a silent invalidation. Harness frames are filtered exactly as an SDK filters its own — they would otherwise leak the local checkout path into a committed fixture.
- **8 of the 25 cases are behavioural** — they return the wrong answer without raising — but an error observatory only ingests errors, and `14` §6.2 requires an `api_event` for every case. Each now runs through to the exception it actually causes in production: the oversell surfaces when the *next* customer checks out, the dropped rows surface when the batched writer indexes an empty chunk, the under-quote surfaces at reconciliation. The surfacing call sites are real features added append-only, so the pinned line numbers in `18` §7 could not move.
- **`fingerprint` is `null` on all 25, deliberately.** S2's algorithm does not exist until T2.3, and a hand-written fingerprint would be a number the implementation is later forced to reproduce by coincidence — if it did not, the "ground truth" would be the thing that was wrong. T2.3 fills them in from the real algorithm; a test asserts they are absent until then and must be inverted when it does.

`resource-01` carries one modelling decision, stated in the case file: a genuine `MemoryError` cannot be produced deterministically or safely, so the payload carries the failure the tenant's tracker really receives — the gateway size cap tripping *after* the peak. The defect being measured is the unbounded accumulation, which the trigger demonstrates directly by showing peak memory scale with the input.

### T3.3 GitHub fixture client

The full `GitHubClient` interface, backed by the local fixture tree. Reads return fixture content; writes record `pull_request_records` with `is_simulated=true`. Same code path as `live`; only the transport differs.

**Accept:** `fetch_file`, `fetch_tree`, `blame`, `compare`, `create_blob/tree/commit/ref`, `create_pull_request` all work against fixtures. Swapping `GITHUB_MODE` changes no application code.

**Done — Phase 5 closes here.** The `GitHubGateway` protocol, the domain types, `FixtureTransport` and the factory, with the GC1–GC12 contract suite from `08` §7.4 parameterised over transports.

- **Object ids are real git object ids**, not invented. GC1 requires a byte-identical `sha` across transports and GitHub returns the git blob id, so plausible-looking hex would pass every test we can write today and diverge the day `live` exists. Blobs, trees and commits all use git's encodings — including its tree-entry sort order, where a directory sorts as though it ended in `/`. Cross-checked against `git hash-object` itself, because testing a reimplementation against the same reimplementation proves only self-consistency.
- **The seam is enforced, not trusted.** `08` §7.1 calls for a lint rule; `test_transport_parity.py` is it. One test forbids naming `github_mode` outside the factory, a second forbids *comparing* against it anywhere — so `/health/ready` may report the mode but cannot quietly become `if settings.github_mode == "fixture"` — and a third forbids importing a transport directly. Verified with a deliberate probe: adding a branch to a worker module fails two of the three.
- **`settings.py` is exempt from the comparison rule, narrowly.** Its two branches are the C5 tier interlocks (`evaluation` refuses `live`, `live` refuses `fixture`), which are safety invariants about what a deployment may touch, not a choice of transport. The no-transport-import rule is what keeps that true.
- **`replay` and `live` raise `TransportUnavailable` rather than silently falling back.** A deployment that asked for `live` and quietly got fixtures would report success for work it never did. They are listed and skipped in the contract suite rather than omitted — an omitted transport is one nobody remembers to add.
- **Settings reach the factory as an object**, typed by a local Protocol, so `roottrace_worker` does not import `roottrace_api` and the attribute access stays in one file.

`create_pull_request` returns a `PullRequestRef` carrying `is_simulated=true`; **persisting the `pull_request_records` row is T8.1** (stage 12 `publish`), which is where the PR body is rendered.

**One limitation, stated rather than hidden:** the fixture tree has a single revision on disk, so `ref` is resolved, validated and recorded but does not select content — asking for `v2.14.1` returns today's bytes. `blame` and `compare` do distinguish revisions, since they read the simulated history. Nothing in V1 reads a historical ref, but a transport that silently returned the wrong revision would be exactly the failure `08` §3.3 warns about, so it is documented at the top of the module.

---

## 6. Week 4 — Retrieval (the hardest week)

### T4.1 Stage 4 — `understand`

Deterministic pre-parse (frame extraction, in-app classification, path normalisation cascade steps 1–2) + exception taxonomy + LLM structured extraction + post-validation.

**Accept:** All 25 fixture errors produce a valid `ErrorUnderstanding`. Frame paths resolve correctly for ≥ 22/25. Exception family is correct for ≥ 23/25.

**Done.** `apps/worker/roottrace_worker/pipeline/understand/`. Measured on the corpus: 25/25 valid, 24/25 frame paths, 23/25 family — the family criterion at the bar with no margin, see below.

**The LLM structured-extraction step is a Protocol, not yet a call.** `03` §S4's algorithm has an LLM step in the middle, and the gateway that makes it possible (T5.1) and the prompt system (T5.2) are both Phase 8 — after retrieval, which `15` §2 and §14 forbid skipping ahead of. `StructuredExtractor` is the seam; its only V1 implementation, `UnavailableExtractor`, raises immediately and the stage takes the exact fallback `03` §S4 already specifies for LLM exhaustion — deterministic pre-parse, `extraction_confidence: 0.5`, continue, never terminal. T5.2 adds an implementation that calls the gateway with `understand/v3.md`; `stage.py`, `validate.py`, the contracts and the plan are unchanged by that addition. Recorded in `03` §S4 under "Implementation note."

**Two fixture cases are recorded misses, not hidden ones.** `race-01` (lost update) and `resource-01` (unbounded growth) both raise an ordinary exception whose type and message say nothing about concurrency or memory — both are knowable only from breadcrumbs, and the deterministic taxonomy deliberately never reads breadcrumbs (fitting the classifier to this corpus would raise the score and teach the pipeline nothing about the twenty-sixth error, `A1` §9). That leaves the family criterion at **23/25 — the bar exactly, with no slack.** `test_exception_family_is_correct_for_at_least_23_of_25` in `tests/integration/test_understand_corpus.py` asserts the miss set by name, so a third case failing is a build break rather than a threshold silently absorbing it. The extractor at T5.2 is expected to close this gap; if it does not, the threshold itself needs revisiting rather than the taxonomy being taught to pattern-match fixture text.

**`expected.exception_family` and `expected.frame_repo_paths` were added to all 25 case files** to make the second and third acceptance criteria measurable — the corpus previously had no ground truth for either. Both were assigned by reading each error, not derived from the resolver they measure; every path was checked to be a real file in the synthetic repository (`test_the_ground_truth_frame_paths_are_real_files`). Schema updated in `14` §6.2 and `A1` §6 in the same commit.

### T4.2 Frame path resolution

The four-step cascade with confidence per method, plus the `test_path_mapping` endpoint.

**Accept:** All four cascade steps are individually exercised and return the documented confidence. Monorepo `root_path` and `service_map` resolution works.

**Done** — the cascade and its confidences. `apps/worker/roottrace_worker/pipeline/retrieve/path_resolution.py`. Steps 1–2 already existed from T4.1 (`understand/frames.py`, no repo access needed); T4.2 adds steps 3–4 — suffix matching against the fetched tree, then filename-only search — and completes the corpus: `config-02`, T4.1's one recorded frame-path miss, now resolves. **All 25/25 cases resolve correctly** with the tree available (`tests/integration/test_retrieve_path_resolution_corpus.py`), up from T4.1's 24/25 measured with S4 alone.

**A step 1/2 result is trusted only once confirmed against the tree, never before.** `config-02` is exactly why: heuristic prefix stripping produces `services/services/export.py`, well-formed and wrong, and S4 had no way to know. `resolve_against_tree` checks every step 1/2 candidate — including the high-confidence configured-mapping case — against the (optionally monorepo-scoped) tree before returning it unmodified, and falls through to suffix and filename search when it isn't there. This also means monorepo scoping (`root_path` + `service_map`) is a hard filter applied even to an otherwise-trusted step 1/2 result: a configured mapping that names a real file *outside* the scoped package is not returned.

**Ambiguity is reported, not resolved by guessing.** Step 4 finding two files with the same basename returns `resolved: null, confidence: 0.30`, not an arbitrary pick — consistent with the rest of the codebase's stance on retrieval (`03` §S5: "we do not guess").

**The `test_path_mapping` HTTP endpoint (`05` §6.6) is not built.** `dry_run_path_mapping` is the endpoint's entire resolution logic as a pure function, matching the documented response shape (`input`/`resolved`/`confidence`/`method`/`exists_in_repo`) — but the route needs `repositories` CRUD (`GET`/`POST`/`PATCH`/`DELETE /v1/repositories`), a `github_installations` binding, and an authorization context, none of which any ticket through Phase 7 builds. T4.2's acceptance criteria are about the cascade and monorepo scoping, not the HTTP surface, and `15` §14's governing rule for this phase is to get retrieval right on the fixture set — building unrelated CRUD now would be exactly the kind of detour that rule warns against. Wiring the route is a few lines once `repositories` endpoints exist (Phase 16 or whichever ticket needs them first); this is a scoping decision made and stated here, not a gap found late.

### T4.3 Stage 5 — retrieval strategies A, B, D, E

Frame-direct fetch, Tree-sitter call-graph expansion (1 hop), git history (blame, recent commits, release diff), test discovery. (Strategy C, vector search, is deferred — the index is empty in V1. The code path exists and returns empty.)

**Accept:** For the running example, retrieval returns `checkout.py`, `tax_client.py`, `routes/checkout.py`, and `test_checkout.py`, plus the introducing commit `8a3f1c2`.

### T4.4 Ranking, budget, and quality scoring

Relevance formula, priority-ordered eviction, 24k hard budget, quality signals, `insufficient_context` termination.

**Accept:** Budget is never exceeded across all 25 cases. Priority 1–2 items are never evicted. The two "unfixable" fixtures terminate as `insufficient_context` without proceeding to reasoning.

> **This is the week to move slowly.** Retrieval correctness determines everything downstream. A wrong context produces a confident wrong answer that passes every later gate.

---

## 7. Week 5 — Reasoning

### T5.1 LLM gateway

Tier routing, provider failover, retry with backoff, structured output with the three-attempt ladder, token and cost accounting, prompt hashing and caching, circuit breaker, outbound secret scanning.

**Accept:** Simulated provider failure fails over correctly. Malformed JSON triggers a repair call on the cheap tier. Every call writes an `llm_calls` row with exact tokens and cost.

### T5.2 Prompt system

Five-layer assembly, untrusted-content fencing, tag neutralisation, instruction-pattern flagging, versioned prompt files, schema derived from Pydantic.

**Accept:** A prompt containing `</untrusted_context>` in the data is escaped. Injection phrases are flagged and recorded on the `llm_calls` row.

### T5.3 Stage 6 — `reason`

Five-step protocol, hypothesis elimination, evidence binding with post-validation, retry-once-then-terminate on binding failure.

**Accept:** ≥ 20/25 fixtures identify the correct root-cause file. 100% of surfaced findings pass evidence validation. A deliberately fabricated citation is rejected.

### T5.4 Stage 7 — `patch`

Diff generation, scope enforcement, in-memory applicability check, regression-test requirement, alternatives recorded.

**Accept:** ≥ 24/25 diffs apply cleanly. Zero diffs touch a forbidden path across all 25 cases. Every case requiring a regression test produces one.

---

## 8. Week 6 — Sandbox

### T6.1 Container images

`roottrace/sandbox-python:3.12` with a warmed wheel cache and pinned analysis toolchain. Digest-pinned base, Trivy-scanned.

**Accept:** Image builds reproducibly. The synthetic repo's dependencies install fully offline.

### T6.2 Orchestration

Create → copy input → start → wait with timeout → read result → force-remove. Semaphore, reaper, transcript capture and sanitisation.

**Accept:** Container removed within 5 s of exit. An orphan created deliberately is reaped within 120 s. Concurrency semaphore holds under 50 queued runs.

### T6.3 Isolation

All eight layers from `07` §3.

**Accept:** All 15 security checks from `07` §12 pass in CI.

### T6.4 The nine gates

G0–G8 with the pre-patch baseline for G4 and G6.

**Accept:** A valid patch passes all gates. A test that passes pre-patch correctly fails G4. Pre-existing test failures are classified `already_failing` and don't count against the patch. Static analysis compares pre/post and counts only new findings.

### T6.4a Measure real sandbox p95 (B11 follow-up)

The 90 s hard kill was derived from summed per-gate budgets, not from measurement. Phase 10 is the first point where a real number exists.

**Record:** observed sandbox p95 and p99 across all 25 fixtures × 3 runs, broken down per gate, with the double-G6 and double-G7 passes attributed separately.

**Decision rule, agreed in advance so it is not re-litigated under delivery pressure:**

| Observed p95 | Action |
|---|---|
| ≤ 45 s | Target holds. No change |
| 45–70 s | Raise the p95 **target** to the observed value; keep the 90 s kill; re-examine the 240 s pipeline SLO in `12` §8 |
| > 70 s | **Revisit the 240 s SLO, not the timeout.** Do not push the kill back down to make the number look better — that reintroduces B11 and presents timeouts as patch-quality failures |

**Accept:** the measurement is recorded in `18` §4 alongside the canonical timings, and any resulting SLO change is applied to `12` §8 and `02` §9 in the same commit.

### T6.5 Degraded mode

Cache-miss handling with honest mode reporting and confidence capping.

**Accept:** Removing a required wheel produces `mode: "partial"`, skipped test gates, and a capped validation component — never a silent pass.

---

## 9. Week 7 — Repair loop and scoring

### T7.1 Stage 9 — `repair`

Gate-specific routing, including G4 → regenerate test only and G5 → return to S6.

**Accept:** Each of the eight gate-specific routes is individually triggered and produces the correct next stage. Three failures terminate as `validation_failed` with all attempts retained.

### T7.2 Stage 10 — `critique`

Separate provider where available, fresh context, seven review dimensions, blocking rules.

**Accept:** The critic receives only the error, the bundle, the diff, and the sandbox results — verified by inspecting the assembled prompt. A deliberately backdoored patch is rejected.

### T7.3 Stage 11 — `score`

Six components, all hard gates, band assignment, publish-mode decision.

**Accept:** Hand-computed expected scores match for 10 constructed scenarios. Every hard gate is individually verified. `build_passed = false` produces `confidence = 0`.

---

## 10. Week 8 — Publish and pipeline viewer

### T8.1 Stage 12 — `publish`

Blob → tree → commit → ref → PR via the fixture client. Full PR description rendering with evidence table, gate table, confidence breakdown, and rejected alternatives.

**Accept:** A `pull_request_records` row is created with a complete, well-formed markdown body. The rendered description matches the template in `03` §S12.

### T8.2 Orchestrator

The full stage loop with durability, idempotency, resumability, WebSocket publishing, and terminal-state handling.

**Accept:** All integration tests from `14` §4.2 pass, including kill-and-resume mid-`reason`.

### T8.3 WebSocket

Hub in `api`, Redis pub/sub, snapshot on connect, per-stage frames, log streaming, heartbeat, reconnect.

**Accept:** A client connecting mid-run receives a snapshot and then live frames. Disconnect and reconnect re-syncs with no lost state.

### T8.4 Dashboard shell + pipeline viewer

Layout, sidebar, top bar, command palette, design tokens, and the pipeline viewer with live stage animation and stage detail panel.

**Accept:** The pipeline animates live during a real run. Every stage is clickable and shows full input/output. Selection is URL state.

---

## 11. Week 9 — Dashboard

| Ticket | Deliverable |
|---|---|
| T9.1 | Overview: KPI tiles, error volume chart with annotations, live pipeline panel, needs-review list, health score |
| T9.2 | Issue list: filters as URL state, sparklines, regression markers, keyboard nav, bulk actions |
| T9.3 | Issue detail: occurrence chart with release annotations, investigation list, sample event |
| T9.4 | Investigation tabs: Evidence (reasoning chain with citations, ruled-out section), Patch (Monaco diff), Sandbox (gates + console), Review, Raw |
| T9.5 | Log explorer: virtualised list, query syntax, detail drawer, redaction indicators |
| T9.6 | Analytics: repeats, pipeline, confidence calibration, cost |
| T9.7 | Settings: general, repositories with path-mapping tester, API keys with reveal-once modal, AI config, audit log |
| T9.8 | States: loading skeletons, empty states, error boundaries, offline banner |

**Accept per ticket:** Playwright coverage; design-review checklist from `10` §10 passes; Lighthouse ≥ 95 on performance and accessibility.

---

## 12. Week 10 — Hardening

### T10.1 Evaluation harness

25 cases × 3 runs, all metrics, baseline comparison, CI gate.

**Accept:** Full run completes in < 15 min. A deliberately degraded prompt is caught by the regression gate.

### T10.2 Security suite

Prompt-injection corpus (25 cases), auth/tenancy tests, sandbox isolation checks.

**Accept:** 25/25 injections blocked. All auth tests pass. All 17 isolation checks pass. All 70 controls in the `11` §13 register have a passing test.

### T10.3 Load testing

The four scenarios from `14` §8.

**Accept:** All pass criteria met. The 5,000 events/s spike drops zero events.

### T10.4 Observability

Metrics, dashboards, alerts, tracing, cost tracking.

**Accept:** All seven dashboards render with real data. Every page-level alert fires correctly in a drill.

### T10.5 Documentation and acceptance

Update every doc to match what was actually built. Run the full V1 acceptance test from `14` §11.

**Accept:** All three acceptance scenarios pass. Every checkbox in `00` §9 is ticked.

---

## 13. Definition of done — V1

- [ ] A fake error traverses all 14 stages with no manual intervention
- [ ] The dashboard renders the run live and every stage is inspectable
- [ ] Root cause is bound to real evidence from the synthetic repo
- [ ] The patch is compiled and tested inside our sandbox
- [ ] A deliberately-broken patch triggers repair and succeeds on attempt 2
- [ ] Confidence is computed from real signals with a visible breakdown
- [ ] A simulated PR record is created with a full description
- [ ] Every run is persisted per user with full history and is replayable
- [ ] The two unfixable fixtures terminate honestly as `insufficient_context`
- [ ] Eval harness: all gated metrics meet threshold
- [ ] Security: 25/25 injections blocked, 17/17 isolation checks pass, RLS verified on 26 tables, all 12 membership escalation tests pass
- [ ] Load: 5,000 events/s spike with zero drops
- [ ] Coverage ≥ 85% line, ≥ 80% branch
- [ ] Lighthouse ≥ 95 performance and accessibility
- [ ] Zero CRITICAL findings on the security checklist in `11` §12

---

## 14. Sequencing rules

| Rule | Reason |
|---|---|
| Schema and RLS before any application code | Retrofitting tenancy is a rewrite |
| Fixtures before the pipeline | You cannot test a pipeline without known-good input |
| Retrieval before reasoning | A reasoning stage fed bad context teaches you nothing about the reasoning stage |
| Sandbox before the repair loop | The loop is driven entirely by sandbox output |
| Scoring before publishing | Publishing is gated on the score |
| Pipeline viewer alongside the orchestrator | Building the viewer while the pipeline is fresh catches contract gaps immediately |
| Eval harness last | It needs the full pipeline to measure anything |

**The one rule that matters most:** do not advance past Week 4 until retrieval quality is genuinely good on all 25 fixtures. Every stage after it inherits its errors, and a confident wrong answer built on wrong context will pass every subsequent gate.

---

## 15. Immediately after V1

| Order | Work | Why |
|---|---|---|
| 1 | Live GitHub mode on one canary repo | Prove the plumbing with the pipeline already trusted |
| 2 | Repo CI as a second gate | Highest-value validation upgrade |
| 3 | Repo indexing + embeddings | Unlocks retrieval strategy C |
| 4 | Sentry / Datadog adapters | Removes the SDK-adoption barrier |
| 5 | Node SDK | Doubles the addressable market |

Full detail in `16-ROADMAP.md`.

---

*Next: [`16-ROADMAP.md`](./16-ROADMAP.md)*
