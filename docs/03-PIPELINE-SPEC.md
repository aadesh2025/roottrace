# 03 — Pipeline Specification

> **This is the core document of the entire system.** Every stage below is a real module you will implement in `apps/worker/roottrace_worker/pipeline/`. The JSON contracts are literal — they become Pydantic models and are validated at every boundary.

---

## 1. Design rules that govern every stage

| Rule | Meaning | Why |
|---|---|---|
| **R1 — Durable** | Every stage writes a `pipeline_step` row before returning | A crash loses at most one stage of work |
| **R2 — Idempotent** | Re-running a completed stage produces identical output and performs no external side effects | Queue redelivery is guaranteed to happen; it must be harmless |
| **R3 — Resumable** | The orchestrator restarts at the first non-completed stage | Never re-pay for 40 seconds of LLM work |
| **R4 — Contract-bound** | Input and output are validated Pydantic models | An LLM returning malformed JSON fails loudly at the boundary, not silently three stages later |
| **R5 — Observable** | Every stage emits duration, token usage, cost, and a WebSocket frame | The pipeline viewer is not instrumentation added later; it is a first-class output |
| **R6 — Budgeted** | Every stage declares a timeout, a token ceiling, and a retry policy | Nothing runs unbounded |
| **R7 — Terminable** | Every stage can end the run with an honest terminal state | `insufficient_context` is a valid, respectable outcome |

---

## 2. Stage map

```
                    ┌──── INGEST PHASE (queue: rt:ingest) ────┐
  S1  receive ──►  S2  fingerprint ──►  S3  triage
                    └─────────────────────────────────────────┘
                                        │ (creates investigation)
                    ┌──── ANALYSIS PHASE (queue: rt:pipeline) ─────────────────┐
                    ▼
              S4  understand  ──►  S5  retrieve  ──►  S6  reason
                                                          │
                    ┌─────────────────────────────────────┘
                    ▼
              S7  patch  ──►  S8  validate (queue: rt:sandbox)
                    ▲              │
                    │              ├─ PASS ──► S10 critique ──► S11 score
                    │              │                                  │
                    └── S9 repair ─┘ FAIL                             │
                       (bounded: max 3)                               │
                    ┌─────────────────────────────────────────────────┘
                    ▼
                    ┌──── DELIVERY PHASE (queue: rt:github) ───┐
              S12 publish ──►  S13 await_decision ──►  S14 feedback
                    └──────────────────────────────────────────┘
```

---

## 3. Investigation state machine

```
                  ┌──────────┐
                  │  queued  │
                  └────┬─────┘
                       ▼
                 ┌───────────┐        ┌──────────────────────┐
                 │ analyzing │───────►│ insufficient_context │ (terminal)
                 └─────┬─────┘        └──────────────────────┘
                       ▼
                 ┌───────────┐
                 │ patching  │
                 └─────┬─────┘
                       ▼
                 ┌───────────┐  fail   ┌───────────┐
                 │validating │◄───────►│ repairing │  (max 3 cycles)
                 └─────┬─────┘  retry  └─────┬─────┘
                       │ pass                │ exhausted
                       ▼                     ▼
                 ┌───────────┐        ┌──────────────────┐
                 │ reviewing │        │ validation_failed│ (terminal)
                 └─────┬─────┘        └──────────────────┘
                       ▼
                 ┌───────────┐  score < floor   ┌──────────────────┐
                 │  scoring  │─────────────────►│ low_confidence   │ (terminal,
                 └─────┬─────┘                  └──────────────────┘  still shown)
                       │ score ≥ floor
                       ▼
                 ┌───────────┐
                 │ publishing│
                 └─────┬─────┘
                       ▼
                 ┌───────────┐    ┌────────┐  ┌──────────┐  ┌─────────────────┐
                 │ awaiting  │───►│ merged │  │ rejected │  │ edited_and_merged│ (terminal)
                 │ decision  │    └────────┘  └──────────┘  └─────────────────┘
                 └───────────┘

   Any state ──► failed (terminal, on unrecoverable error)
   Any state ──► cancelled (terminal, on user action)
```

Terminal states that are still shown prominently in the UI: `insufficient_context`, `validation_failed`, `low_confidence`. **These are honest outcomes, not hidden failures.** A system that says "I couldn't determine this with confidence" is more trustworthy than one that always produces an answer.

---

## 4. The stages

Each stage below documents: purpose, trigger, input contract, algorithm, output contract, failure modes, budget.

---

### S1 — `receive`

**Purpose:** Accept, authenticate, sanitise, and durably persist an incoming error event.
**Runs in:** `api` service (synchronous, in the request path).
**Target p95:** 50 ms · **Hard timeout:** 2 s · **Retries:** 0 (the client retries) · **On failure:** 5xx with `request_id`; nothing persisted.

#### Input

```jsonc
// POST /v1/events
{
  "events": [
    {
      "event_id": "evt_client_generated_uuid",     // optional; server generates if absent
      "timestamp": "2026-08-04T09:14:22.481Z",     // ISO-8601 UTC, required
      "environment": "production",                  // production|staging|development
      "service": "checkout-api",                    // logical service name
      "release": "v2.14.3",                         // optional; enables release correlation
      "level": "error",                             // error|fatal|warning

      "error": {
        "type": "TypeError",                        // exception class name, required
        "message": "unsupported operand type(s) for +: 'decimal.Decimal' and 'NoneType'",
        "stack_trace": "Traceback (most recent call last):\n  File \"/app/services/checkout.py\", line 142, in calculate_total\n    subtotal = base_price + tax_amount\nTypeError: ...",
        "stack_frames": [                           // optional; SDK pre-parses when it can
          {
            "file": "/app/services/checkout.py",
            "line": 142,
            "column": 19,
            "function": "calculate_total",
            "in_app": true,                         // false for site-packages/node_modules
            "context_line": "    subtotal = base_price + tax_amount",
            "pre_context": ["def calculate_total(cart, user):", "    base_price = cart.subtotal()"],
            "post_context": ["    return subtotal * 1.0", ""],
            "vars": { "base_price": "Decimal('49.99')", "tax_amount": "None" }  // redacted
          }
        ]
      },

      "request": {                                  // optional but high value
        "method": "POST",
        "url": "/api/v2/checkout",
        "route_pattern": "/api/v2/checkout",        // pre-templated; avoids ID cardinality
        "status_code": 500,
        "duration_ms": 412,
        "headers": { "content-type": "application/json" },   // allowlisted only
        "query_params": { "coupon": "SAVE20" },
        "body_sample": "{\"cart_id\":\"...\"}"       // truncated to 4 KB, redacted
      },

      "runtime": {
        "language": "python",
        "language_version": "3.12.4",
        "framework": "fastapi",
        "framework_version": "0.111.0",
        "os": "linux",
        "hostname": "checkout-api-7d9f-x4k2"
      },

      "user_context": {                             // pseudonymous only
        "user_hash": "u_9f2b1c",                    // SDK-side hash; we never receive PII
        "plan": "pro",
        "is_authenticated": true
      },

      "breadcrumbs": [                              // last N events before the error
        { "ts": "2026-08-04T09:14:22.101Z", "category": "db",   "message": "SELECT cart WHERE id=? (12ms)" },
        { "ts": "2026-08-04T09:14:22.340Z", "category": "http", "message": "GET tax-service/rate → 503" }
      ],

      "tags": { "region": "eu-west-1", "tenant_tier": "enterprise" },
      "extra": { "cart_item_count": 3 }
    }
  ]
}
```

> **Note on `breadcrumbs`:** these are disproportionately valuable. In the example above, the `503` from the tax service is the actual root cause — the `TypeError` is a downstream symptom. Retrieval alone would never find that; the breadcrumb does.

#### Algorithm

```
1. Extract Bearer token → resolve API key
   ├─ Redis cache hit (60s TTL) → project_id, plan, scopes
   └─ miss → SELECT from api_keys WHERE key_hash = sha256(token) AND revoked_at IS NULL
             (constant-time compare; cache result)
2. Rate limit: token bucket per key. 429 with Retry-After on exhaustion.
3. Idempotency — ATOMIC CLAIM, never check-then-act (see B7 below):
       SET idem:{project_id}:{key} = "in_flight" NX EX 86400
   ├─ claimed        → we own this request; proceed
   ├─ value in_flight→ a concurrent duplicate is mid-flight → 409 RT-CONFLICT-0004
   └─ value = <resp> → replay: return the stored response verbatim
4. Batch size guard: reject >100 events with RT-INGEST-0003.
5. Per-event JSON Schema validation. Invalid events are rejected individually;
   the batch is NOT failed wholesale. Response reports per-index errors.
6. Sanitisation pass (see §4.S1.sanitise below).
7. INSERT INTO raw_events (batch, one statement).
8. PUT full payload → object storage at raw/{project_id}/{yyyy}/{mm}/{dd}/{event_id}.json.gz
9. enqueue rt:ingest per accepted event.
10. Overwrite idem:{project_id}:{key} with the serialised response (24h TTL),
    releasing the in_flight claim from step 3.
11. Return 202.

On any failure between 3 and 10, the claim is DELETED so the client's retry can
proceed. A crashed worker leaves the claim to expire at 24h; the client receives
409 until then, which is correct — we cannot prove the batch was not persisted.
```

> **B7 — why the claim must be atomic.** A plain "read the key, and if absent proceed" is check-then-act: two concurrent retries of the same batch both observe an absent key, both pass, and both insert. The result is duplicated `raw_events`, an inflated `occurrence_count`, and — if the duplicate crosses the S3 gate — a second paid pipeline run. `SET … NX` collapses the check and the claim into one atomic operation, which is the only formulation that is safe under concurrency.

#### Sanitisation (runs before anything is stored)

| Check | Action |
|---|---|
| Known secret patterns (AWS keys, GitHub tokens, JWTs, private key headers, `sk-*`) | Replace with `[REDACTED:aws_key]` etc. |
| High-entropy strings (Shannon > 4.5, length > 20, in a value position) | Replace with `[REDACTED:high_entropy]` |
| Email addresses in message/body | Replace with `[REDACTED:email]` |
| Credit-card-shaped numbers (Luhn-valid) | Replace with `[REDACTED:pan]` |
| Header allowlist | Drop everything not in the allowlist (`Authorization` never stored) |
| Field size caps | `message` 8 KB, `stack_trace` 64 KB, `body_sample` 4 KB — truncate with marker |

Redactions are recorded in `raw_events.redactions jsonb` so the UI can show *that* something was redacted without showing *what*.

#### Output

```jsonc
// 202 Accepted
{
  "batch_id": "bat_01J2K3...",
  "accepted": 98,
  "rejected": 2,
  "errors": [
    { "index": 14, "code": "RT-INGEST-0011", "message": "error.type is required" },
    { "index": 71, "code": "RT-INGEST-0012", "message": "timestamp is more than 7 days in the past" }
  ]
}
```

#### Failure modes

| Condition | Code | HTTP |
|---|---|---|
| Missing/invalid API key | `RT-AUTH-0001` | 401 |
| Key revoked | `RT-AUTH-0004` | 401 |
| Rate limit exceeded | `RT-RATE-0001` | 429 |
| Batch > 100 events | `RT-INGEST-0003` | 400 |
| Payload > 5 MB | `RT-INGEST-0004` | 413 |
| Project quota exhausted | `RT-QUOTA-0001` | 402 |
| All events invalid | `RT-INGEST-0010` | 422 |
| Idempotency key claimed by an in-flight duplicate | `RT-CONFLICT-0004` | 409 |
| Idempotency key reused with a different body | `RT-CONFLICT-0001` | 409 |

---

### S2 — `fingerprint`

**Purpose:** Collapse identical errors into a single Issue so a 10,000-occurrence storm becomes one investigation, not ten thousand.
**Runs in:** `worker`, queue `rt:ingest`.
**Target p95:** 100 ms · **Hard timeout:** 1 s · **Retries:** 3, exponential backoff · **On exhaustion:** dead-letter; the occurrence is preserved and reprocessable.

#### Why this is harder than it looks

Naive fingerprinting on `type + message` fails immediately, because messages contain variable data:

```
"User 8821 not found"    ─┐
"User 9134 not found"     ├─ must all be ONE issue
"User 77 not found"      ─┘
```

Naive fingerprinting on the full stack trace also fails, because line numbers shift with every unrelated commit, and the same bug fingerprints differently after a formatting change.

#### Algorithm

```
fingerprint_input = [
    error.type,                              # "TypeError"
    normalize_message(error.message),        # see below
    top_in_app_frames(stack_frames, n=5),    # file + function only — NOT line numbers
    request.route_pattern or ""              # "/api/v2/checkout"
]
fingerprint = sha256("\x1f".join(fingerprint_input))[:32]
```

**`normalize_message`** applies, in order:

| Pattern | Replacement |
|---|---|
| UUIDs | `<uuid>` |
| Hex ≥ 8 chars | `<hex>` |
| Integers ≥ 3 digits | `<num>` |
| Quoted strings | `<str>` |
| Absolute file paths | `<path>` |
| IPv4/IPv6 | `<ip>` |
| ISO timestamps | `<ts>` |
| Email addresses | `<email>` |
| URLs | `<url>` |
| Memory addresses `0x...` | `<addr>` |

**`top_in_app_frames`** takes the deepest 5 frames where `in_app = true`, reduced to `basename(file) + "::" + function`. Excluding line numbers is the key decision: it makes the fingerprint stable across refactors while still distinguishing genuinely different code paths.

#### Custom fingerprint rules (per project, V1 supports these)

```jsonc
{
  "fingerprint_rules": [
    { "match": { "error.type": "HTTPError" },
      "group_by": ["error.type", "request.route_pattern", "request.status_code"] },
    { "match": { "service": "worker-*" },
      "group_by": ["error.type", "frames[0].function"] }
  ]
}
```

#### Output

```jsonc
{
  "fingerprint": "a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5",
  "issue_id": "iss_01J2K...",
  "is_new_issue": false,
  "occurrence_count": 1_247,
  "first_seen": "2026-07-28T14:02:11Z",
  "last_seen": "2026-08-04T09:14:22Z",
  "rate_per_hour": 41.3,
  "environments": ["production", "staging"],
  "affected_releases": ["v2.14.1", "v2.14.3"],
  "regression": true,          // was resolved, now recurring
  "regressed_from": "inv_01J1..."
}
```

The upsert is a single atomic statement — this is the hot path during a storm:

```sql
insert into issues (id, project_id, fingerprint, error_type, normalized_message,
                    first_seen, last_seen, occurrence_count, status)
values (:id, :project_id, :fp, :type, :msg, :ts, :ts, 1, 'open')
on conflict (project_id, fingerprint) do update
  set last_seen        = excluded.last_seen,
      occurrence_count = issues.occurrence_count + 1,
      status           = case when issues.status = 'resolved' then 'regressed'
                              else issues.status end
returning id, occurrence_count, first_seen, (xmax = 0) as is_new;
```

---

### S3 — `triage`

**Purpose:** Decide severity, and decide whether this occurrence deserves a *new* investigation or should attach to an existing one.
**Target p95:** 200 ms · **Hard timeout:** 1 s · **Retries:** 3 · **On exhaustion:** dead-letter. A `UniqueViolation` is **not** a failure — it routes to `already_investigating`.

#### Severity scoring

```
severity_score =
      w_rate       * normalize(rate_per_hour,   0..500)     # 0.30
    + w_users      * normalize(affected_users,  0..1000)    # 0.25
    + w_criticality* endpoint_criticality                   # 0.20  (project config)
    + w_env        * environment_weight                     # 0.15  (prod 1.0, staging 0.4, dev 0.1)
    + w_novelty    * (1.0 if is_new_issue else 0.2)         # 0.10

P0: ≥ 0.80    P1: ≥ 0.60    P2: ≥ 0.35    P3: < 0.35
```

`endpoint_criticality` is a per-project map, defaulting to 0.5:

```jsonc
{ "endpoint_criticality": {
    "/api/v2/checkout":  1.0,
    "/api/v2/auth/*":    0.9,
    "/api/v2/search":    0.4,
    "/health":           0.0 } }
```

#### Investigation gating

An occurrence creates a **new** investigation only if all of these hold:

| Condition | Rationale |
|---|---|
| No investigation for this fingerprint in a non-terminal state | Never run two pipelines for the same bug |
| No investigation for this fingerprint completed within the cooldown window (default 6 h) | Prevents re-investigating an already-answered bug |
| `severity >= project.min_investigation_severity` (default P2) | Don't spend money on P3 noise |
| Project has quota remaining | Hard cost control |
| `environment` is in `project.investigated_environments` (default `["production"]`) | Don't burn budget on dev noise |
| Issue is not muted | User control |

If gated, the occurrence still attaches to the issue and appears in analytics — we just don't launch a pipeline.

#### The gate is advisory; the database is authoritative (B8)

Every condition above is a **read**. Two occurrences of the same fingerprint arriving in the same instant both evaluate the gate, both see no active investigation, and both proceed — producing two pipelines, two PRs, and double cost for one bug. Reads cannot enforce mutual exclusion.

The enforcement is the partial unique index defined in `04` §8:

```sql
create unique index investigations_one_active_per_issue
  on investigations (issue_id)
  where status in ('queued','analyzing','patching','validating','repairing',
                   'reviewing','scoring','publishing');
```

S3 therefore always attempts the insert and handles the conflict rather than trusting its own check:

```
try:
    INSERT INTO investigations (...) VALUES (...)
except UniqueViolation:                     # another occurrence won the race
    attach_occurrence_to_existing_investigation()
    return TriageResult(should_investigate=False,
                        gate_reason="already_investigating")
```

The losing occurrence is **not** an error. It attaches to the winner exactly as a gated occurrence does, and the outcome is indistinguishable from having lost the check-then-act race — which is the point.

#### Output

```jsonc
{
  "severity": "P1",
  "severity_score": 0.72,
  "severity_factors": {
    "rate": 0.24, "users": 0.18, "criticality": 0.20, "environment": 0.15, "novelty": 0.02
  },
  "should_investigate": true,
  "gate_reason": null,        // "cooldown_active" | "quota_exhausted" | "muted"
                              // | "below_min_severity" | "environment_excluded"
                              // | "already_investigating"  ← lost the insert race
  "investigation_id": "inv_01J2K..."
}
```

---

### S4 — `understand`

**Purpose:** Convert an unstructured error into a precise, machine-usable structure — and, crucially, into a **retrieval plan**. This stage decides what stage 5 will go and fetch.
**Model:** fast tier (cheap, structured extraction — this is not a reasoning task).
**Target p95:** 3 s · **Hard timeout:** 10 s · **Token cap:** 2,000 out · **Retries:** 2 (the second uses the schema-repair prompt) · **On exhaustion:** fall back to the deterministic pre-parse with `extraction_confidence: 0.5` and continue — never terminal.

#### Algorithm

```
1. Deterministic pre-parse (no LLM):
   ├─ Language detection from runtime.language or stack trace shape
   ├─ Frame extraction with a language-specific regex/parser
   ├─ in_app classification: exclude site-packages, node_modules, dist-packages,
   │  stdlib paths, vendor/, .venv/
   ├─ Path normalisation: strip container prefixes (/app/, /usr/src/app/, /workspace/)
   │  using the project's configured path_mappings
   └─ Exception taxonomy lookup (see table below)

2. LLM structured-extraction call over (pre-parse output + message + breadcrumbs + request):
   → produces the ErrorUnderstanding object

3. Deterministic post-validation:
   ├─ every suspected_file must have plausibly-repo-relative shape
   ├─ every claim must reference a frame index that exists
   └─ on violation → repair prompt (1 retry) → on second violation, drop the claim
```

#### Exception taxonomy (deterministic priors, injected into the prompt)

| Family | Examples | Typical cause class | Retrieval hint |
|---|---|---|---|
| Null/undefined | `TypeError: NoneType`, `TypeError: undefined is not a function`, `NullPointerException` | Missing guard on optional value, upstream returned null | Fetch the producer of the null value, not just the consumer |
| Type mismatch | `TypeError`, `ValueError`, `ClassCastException` | Contract drift between modules | Fetch both sides of the boundary |
| Key/index | `KeyError`, `IndexError`, `AttributeError` | Shape assumption violated | Fetch where the structure is built |
| Integration | `ConnectionError`, `TimeoutError`, HTTP 5xx from a dependency | External failure, missing retry/fallback | Fetch the client wrapper and its config |
| Data/DB | `IntegrityError`, `DoesNotExist`, deadlock | Constraint or migration mismatch | Fetch the model and recent migrations |
| Concurrency | Race, deadlock, `RuntimeError: event loop` | Shared mutable state, missing lock | Fetch all writers to the shared resource |
| Resource | `MemoryError`, `OSError: too many open files` | Leak or unbounded growth | Fetch the allocation site and its lifecycle |
| Auth | `PermissionError`, 401/403 | Missing/expired credential, scope mismatch | Fetch the auth middleware |
| Serialization | `JSONDecodeError`, `ValidationError` | Malformed input, schema drift | Fetch the schema definition and the parser |

#### Output contract

```jsonc
{
  "language": "python",
  "framework": "fastapi",
  "exception": {
    "type": "TypeError",
    "family": "null_undefined",
    "message_normalized": "unsupported operand type(s) for +: '<type>' and '<type>'",
    "is_user_facing": true
  },
  "frames": [
    {
      "index": 0,
      "raw_path": "/app/services/checkout.py",
      "repo_path": "services/checkout.py",       // after path_mappings
      "line": 142,
      "function": "calculate_total",
      "in_app": true,
      "confidence": 0.95                          // confidence in the path mapping
    },
    { "index": 1, "raw_path": "/app/api/routes/checkout.py", "repo_path": "api/routes/checkout.py",
      "line": 58, "function": "create_checkout", "in_app": true, "confidence": 0.95 }
  ],
  "entry_point": { "type": "http_route", "method": "POST", "pattern": "/api/v2/checkout",
                   "handler": "api/routes/checkout.py::create_checkout" },
  "failure_point": { "repo_path": "services/checkout.py", "function": "calculate_total", "line": 142 },

  "implicated_symbols": ["calculate_total", "base_price", "tax_amount", "create_checkout"],

  "initial_hypotheses": [
    { "statement": "tax_amount is None because the tax service returned 503 and the error path returns None instead of raising",
      "prior": 0.65,
      "evidence_needed": ["the tax service client implementation", "the caller of calculate_total"] },
    { "statement": "cart has no tax configuration for this region and a lookup returned None",
      "prior": 0.25,
      "evidence_needed": ["tax configuration model", "region resolution logic"] },
    { "statement": "a recent change altered the return type of the tax calculation",
      "prior": 0.10,
      "evidence_needed": ["recent commits touching tax logic"] }
  ],

  "retrieval_plan": {
    "must_fetch": ["services/checkout.py", "api/routes/checkout.py"],
    "should_fetch_by_symbol": ["calculate_total", "get_tax_rate", "TaxClient"],
    "semantic_queries": ["tax rate calculation and fallback handling",
                         "checkout total computation"],
    "want_git_history_for": ["services/checkout.py"],
    "want_tests_for": ["calculate_total"],
    "breadcrumb_signal": "GET tax-service/rate returned 503 141 ms before the error"
  },

  "notes": "The breadcrumb showing a 503 from tax-service immediately before the failure is the strongest available signal; prioritise retrieving the tax client's error handling.",
  "extraction_confidence": 0.91,
  "flags": []
}
```

`flags` (added at T4.1) is where the failure-mode table below becomes machine-readable rather than only prose: `low_frame_confidence`, `no_in_app_frames`, `no_stack_trace`, `deterministic_only`, and `suspicious_content_detected` (`A2` §2 rule 5 — instruction-shaped text found in untrusted input, recorded and never obeyed). The dashboard reads this list directly instead of re-deriving it from the rest of the object.

#### Failure modes

| Condition | Behaviour |
|---|---|
| No `in_app` frames at all | Continue with entry point from `request.route_pattern`; flag `low_frame_confidence` |
| Path mapping produces no plausible repo path | Set frame `confidence: 0.3`; S5 falls back to filename search across the tree |
| LLM returns invalid JSON twice | Fall back to deterministic pre-parse only, `extraction_confidence: 0.5`, continue |
| Stack trace absent entirely | Semantic-only retrieval path; flag prominently in the UI |

#### Implementation note — the algorithm spans two stages, not one (added at T4.1)

Step 2 above is the LLM structured-extraction call, and it depends on the LLM gateway (T5.1) and the prompt system (T5.2), both Phase 8. Phase 7 (retrieval) is built and must be validated first (`15` §2, §14) — so T4.1 implements steps 1 and 3 as a permanent, standalone pass and step 2 behind a `StructuredExtractor` seam whose only V1 implementation reports itself unavailable. This is not a placeholder standing in for missing behaviour: it is the literal failure-mode row above — *"LLM returns invalid JSON twice → fall back to deterministic pre-parse only, `extraction_confidence: 0.5`, continue"* — taken deliberately rather than only on exhaustion. T5.2 adds an implementation that calls the gateway with `understand/v3.md`; nothing else in the stage changes.

The same reasoning splits the frame-path cascade in `08` §3.2 across two stages. §8.1 already states the boundary — *"S4 fetching a file to 'check' a path" is a boundary violation; S4 has no repo access by design; it produces a plan"* — and this table's own second row says where the unresolved case goes: S5's tree search. T4.1 therefore implements cascade steps 1–2 (configured mappings, heuristic prefix stripping) here; steps 3–4 (suffix match against the fetched tree, filename search) are T4.2's, on the S5 side.

---

### S5 — `retrieve`

**Purpose:** Assemble the minimum sufficient code context. **This stage determines whether the whole system works.** A perfect model with wrong context produces a confident wrong answer.
**Target p95:** 8 s · **Hard timeout:** 20 s · **Context cap:** 24,000 tokens (hard, P3) · **Retries:** 2, GitHub 5xx only · **On exhaustion or thin context:** terminal `insufficient_context`.

#### The five retrieval strategies, run in parallel

```
                       retrieval_plan (from S4)
                                │
        ┌───────────┬───────────┼───────────┬────────────┐
        ▼           ▼           ▼           ▼            ▼
   ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌──────────┐
   │ A       │ │ B       │ │ C       │ │ D       │ │ E        │
   │ Frame   │ │ Call-   │ │ Vector  │ │ Git     │ │ Test     │
   │ direct  │ │ graph   │ │ semantic│ │ history │ │ discovery│
   │ fetch   │ │ expand  │ │ search  │ │         │ │          │
   └────┬────┘ └────┬────┘ └────┬────┘ └────┬────┘ └────┬─────┘
        └───────────┴───────────┼───────────┴───────────┘
                                ▼
                   ┌────────────────────────┐
                   │  Rank · Dedupe · Trim  │
                   │  to token budget       │
                   └───────────┬────────────┘
                               ▼
                        ContextBundle
```

**A — Frame-direct fetch (weight 1.00)**
For each `in_app` frame, fetch the file via GitHub Contents API at the SHA matching the release, falling back to default branch HEAD. Extract the enclosing function ±40 lines. If the file is < 400 lines, take the whole file — surrounding code is usually worth more than the tokens it costs.

**B — Call-graph expansion (weight 0.85)**
Parse each fetched file with Tree-sitter. Build a local symbol table. For the failure-point function, resolve:
- **Callees** — every function it calls. Fetch their definitions (1 hop; 2 hops only if budget remains).
- **Callers** — who calls it, found via the pre-built `code_edges` table if the repo is indexed, otherwise via GitHub code search on the symbol name.
- **Type definitions** — classes/dataclasses/interfaces referenced in the signature or body.
- **Imports** — resolve local imports to repo paths and fetch those that define implicated symbols.

**C — Vector semantic search (weight 0.70)**
Embed each `semantic_queries` string, then `pgvector` cosine search over `code_nodes.embedding` scoped to the project. Take top-8 above a 0.72 similarity floor. This is the strategy that finds code the stack trace never mentions — for instance the tax-client fallback in our running example.

```sql
select id, repo_path, symbol_name, start_line, end_line, source,
       1 - (embedding <=> :query_vec) as similarity
from code_nodes
where project_id = :pid
  and repository_id = :rid
  and 1 - (embedding <=> :query_vec) > 0.72
order by embedding <=> :query_vec
limit 8;
```

**D — Git history (weight 0.60)**
- `git blame` equivalent on the failing line range → the commit that introduced it, author, date, message.
- Last 10 commits touching any `must_fetch` file.
- Any open or recently-merged PR touching those paths.
- **Release correlation:** if the error first appeared at `v2.14.3`, diff `v2.14.2..v2.14.3` restricted to implicated paths. This is frequently decisive and cheap.

**E — Test discovery (weight 0.55)**
Locate tests referencing the implicated symbols by convention (`test_<name>.py`, `<name>.test.ts`, `__tests__/`, `tests/`) and by symbol grep. Their presence tells the patch stage what to preserve; their absence tells the sandbox stage it must generate a regression test.

#### Ranking and budget enforcement

```
relevance(item) = strategy_weight
                × recency_factor        # commits/PRs decay over 90 days
                × proximity_factor      # 1.0 at failure point, 0.8 at 1 hop, 0.6 at 2 hops
                × (1 + 0.15 * symbol_overlap_with_implicated_symbols)
```

Items are sorted by relevance and admitted until the 24,000-token budget is consumed. Eviction is priority-ordered:

| Priority | Never evicted | Notes |
|---|---|---|
| 1 | The failure-point function | Non-negotiable |
| 2 | The entry-point handler | Non-negotiable |
| 3 | Direct callees of the failure point | |
| 4 | Type/class definitions in scope | |
| 5 | Direct callers | |
| 6 | Blame commit + release diff | |
| 7 | Vector-search results | First to be trimmed |
| 8 | Tests | Trimmed to signatures only if tight |
| 9 | Second-hop graph | Dropped first |

If, after admitting priority 1–4, we still hold fewer than 3 distinct files or fewer than 800 tokens of `in_app` source, the stage terminates the investigation as **`insufficient_context`** with an explanation. We do not guess.

#### Output contract

```jsonc
{
  "bundle_id": "ctx_01J2K...",
  "repository": { "id": "repo_01J...", "full_name": "acme/checkout-api",
                  "commit_sha": "9f2b1c4e...", "ref": "v2.14.3" },
  "token_count": 18_412,
  "token_budget": 24_000,
  "files": [
    {
      "repo_path": "services/checkout.py",
      "strategy": "frame_direct",
      "relevance": 1.0,
      "language": "python",
      "content": "…",
      "line_range": [100, 190],
      "truncated": false,
      "symbols_defined": ["calculate_total", "apply_discount"],
      "blame": { "line": 142, "commit": "8a3f...", "author": "dana@acme.io",
                 "date": "2026-07-25T11:04:00Z",
                 "message": "refactor: extract tax lookup into TaxClient" }
    },
    { "repo_path": "clients/tax_client.py", "strategy": "vector_semantic", "relevance": 0.79,
      "content": "…", "line_range": [1, 68], "symbols_defined": ["TaxClient", "get_rate"] }
  ],
  "graph": {
    "nodes": [
      { "id": "services/checkout.py::calculate_total", "kind": "function", "is_failure_point": true },
      { "id": "clients/tax_client.py::TaxClient.get_rate", "kind": "method" },
      { "id": "api/routes/checkout.py::create_checkout", "kind": "function", "is_entry_point": true }
    ],
    "edges": [
      { "from": "api/routes/checkout.py::create_checkout",
        "to": "services/checkout.py::calculate_total", "kind": "calls" },
      { "from": "services/checkout.py::calculate_total",
        "to": "clients/tax_client.py::TaxClient.get_rate", "kind": "calls" }
    ]
  },
  "history": {
    "blame_commit": { "sha": "8a3f...", "message": "refactor: extract tax lookup into TaxClient",
                      "date": "2026-07-25T11:04:00Z", "author": "dana@acme.io" },
    "recent_commits": [ /* … */ ],
    "release_diff": { "from": "v2.14.2", "to": "v2.14.3", "files_changed": 3,
                      "relevant_hunks": [ /* … */ ] },
    "open_prs": []
  },
  "tests": {
    "found": [ { "repo_path": "tests/test_checkout.py",
                 "covers": ["calculate_total"], "content": "…" } ],
    "coverage_estimate": "partial"
  },
  "strategy_stats": {
    "frame_direct":    { "items": 2, "tokens": 6_200 },
    "call_graph":      { "items": 4, "tokens": 5_800 },
    "vector_semantic": { "items": 3, "tokens": 3_100 },
    "git_history":     { "items": 5, "tokens": 2_100 },
    "test_discovery":  { "items": 1, "tokens": 1_212 }
  },
  "quality": {
    "score": 0.86,
    "signals": {
      "failure_point_resolved": true,
      "entry_point_resolved": true,
      "callees_resolved": 4,
      "callers_resolved": 1,
      "has_tests": true,
      "has_release_correlation": true,
      "unresolved_symbols": ["get_regional_config"]
    }
  },
  "gaps": ["get_regional_config could not be located in the repository"]
}
```

`quality.score` feeds directly into the final confidence calculation (S11). Poor retrieval must lower confidence — this is the mechanism that prevents a confident answer built on thin context.

#### Implementation note — strategies A, B, D, E built at T4.3; ranking is T4.4 (added at T4.3)

Four of the five strategies are implemented in `apps/worker/roottrace_worker/pipeline/retrieve/strategies.py`, run via `gather(...)`. Strategy C (vector semantic search) is deferred exactly as this section already specifies — the code path exists (`strategy_c_vector_semantic`) and returns empty, since `code_nodes.embedding` is never populated in V1.

**Strategy B parses with Python's `ast`, not Tree-sitter.** V1's corpus, fixture repository, and sandbox are all Python-only; `ast.parse` is the boring, zero-dependency, first-class way to do what strategy B needs for one language, and Tree-sitter's payoff — uniformity across languages — only starts to matter at V5 (Go/Java/Ruby, `18` §8). Introducing a compiled-grammar dependency now, for a language V1 parses natively already, would be paying that cost years before it earns anything. `apps/worker/roottrace_worker/pipeline/retrieve/ast_index.py`.

**Every path S4 produced is re-verified against the tree before use, never trusted.** `understanding.frames[].repo_path` and `understanding.failure_point.repo_path` are cascade steps 1–2 only (`03` §S4 has no repo access to check them against) — `config-02` is a well-formed path from those steps that is not a real file, and strategy A/B blindly fetching it would silently drop the frame or return nothing rather than finding the real one via T4.2's `resolve_against_tree`.

**`search_symbol`'s contract widened at T4.3 to make "callers" findable at all.** `code_edges` is never populated in V1, so *"found via ... GitHub code search on the symbol name"* is the only path this stage ever takes for callers — and a caller is a *use* of a name, not a second definition of it. `08` §3.2 records the resulting contract: every textual occurrence, including comments and docstrings, classified `"function"`/`"class"`/`"reference"`; precision (confirming a `"reference"` hit is a genuine call, not a stray mention) is the caller's job, done here with an `ast` parse of the candidate file.

**This is the ticket that reaches `clients/tax_client.py`.** T4.1 and T4.2 could name every file a stack trace or a resolved path could point to; neither could reach a file with no frame, no breadcrumb, and no mention in the message. Strategy B's one-hop callee expansion — `calculate_total` calls `get_rate`, `get_rate` is defined in `clients/tax_client.py` — is what closes that gap, proving `03` §S5's premise that call-graph expansion, not frame-direct fetch, is what makes the running example work at all.

**Three corpus cases have a root cause no strategy here can reach**, for three distinct, structural reasons rather than one bug: `regression-02`'s root cause is two hops from the failure point (T4.3 does one, per this section's "1 hop; 2 hops only if budget remains" — there is no budget concept until T4.4); `config-02`'s root cause is the producer of a value injected at composition-root time, reached by no call edge at all; `type-mismatch-03`'s root cause and its failure point are unrelated sibling functions connected only through shared mutable data, never through a call. Recorded and asserted by name in `tests/integration/test_retrieve_strategies_corpus.py::ROOT_CAUSE_UNREACHABLE_BY_T4_3`, so a fourth case joining the set — or one of these three starting to resolve — is a build break, not a silent drift.

---

### S6 — `reason`

**Purpose:** Determine the root cause, with evidence, from the context bundle.
**Model:** reasoning tier (the most expensive call in the pipeline; it is where the value is).
**Target p95:** 25 s · **Hard timeout:** 60 s · **Token cap:** 4,000 out · **Retries:** 2 · **On evidence-binding failure:** one correction retry, then terminal `insufficient_context`.

#### The reasoning protocol

We do not ask "what's the bug?" We enforce a structure that makes the model's work checkable:

```
Step 1  OBSERVE     State only what the evidence literally shows. No inference yet.
Step 2  HYPOTHESISE Generate 2–4 candidate causes with prior probabilities.
Step 3  TEST        For each hypothesis, cite the specific retrieved evidence that
                    supports OR contradicts it. A hypothesis with no supporting
                    evidence is eliminated, not carried forward.
Step 4  CHAIN       For the surviving hypothesis, walk why → why → why until reaching
                    a cause that is actionable in code (not "the tax service was down"
                    but "the tax client swallows 5xx and returns None").
Step 5  CONCLUDE    State root cause, mechanism, blast radius, and the minimal fix
                    location.
```

**Why the chain matters.** Stopping at step 1 gives "`tax_amount` is None." That is a *symptom restatement*, not a root cause, and a patch built on it produces `if tax_amount is None: tax_amount = 0` — which silently charges customers no tax. Walking the chain reaches "the tax client converts a 503 into a `None` return instead of raising," which produces the correct fix.

#### Hard rule — evidence binding

Every `finding` must carry an `evidence` array where each entry references a real artefact in the context bundle. The post-validator checks:

- `repo_path` exists in `bundle.files`
- `line_range` falls within that file's retrieved range
- quoted `excerpt` matches the actual retrieved source (normalised whitespace)
- referenced `commit_sha` exists in `bundle.history`

**Any finding failing validation is discarded before it reaches the user.** If the primary root-cause finding fails validation, the stage retries once with an explicit correction prompt; if it fails again, the investigation terminates as `insufficient_context`. This is the single most important anti-hallucination mechanism in the system.

#### Output contract

```jsonc
{
  "root_cause": {
    "summary": "TaxClient.get_rate() catches HTTPError and returns None on any non-200 response, so a 503 from the tax service silently yields None, which calculate_total() then adds to a Decimal.",
    "mechanism": "tax-service returns 503 → TaxClient.get_rate catches httpx.HTTPStatusError at clients/tax_client.py:41 and returns None → calculate_total receives None at services/checkout.py:138 with no guard → line 142 evaluates Decimal + None → TypeError",
    "category": "unhandled_error_path",
    "introduced_by": { "commit": "8a3f...", "date": "2026-07-25T11:04:00Z",
                       "author": "dana@acme.io",
                       "note": "the refactor that extracted TaxClient replaced a raise with a return None" },
    "blast_radius": {
      "affected_endpoints": ["/api/v2/checkout"],
      "affected_functions": ["services/checkout.py::calculate_total"],
      "other_callers_at_risk": ["services/quote.py::estimate_total"],
      "severity_justification": "Every checkout attempt fails while the tax service is degraded; there is no fallback."
    }
  },

  "reasoning_chain": [
    { "step": 1, "type": "observe",
      "statement": "The exception is raised at services/checkout.py:142 where base_price (Decimal) is added to tax_amount.",
      "evidence": [ { "kind": "file", "repo_path": "services/checkout.py", "line_range": [140,143],
                      "excerpt": "    subtotal = base_price + tax_amount" } ] },
    { "step": 2, "type": "observe",
      "statement": "tax_amount is assigned from TaxClient.get_rate() at line 138 with no None check.",
      "evidence": [ { "kind": "file", "repo_path": "services/checkout.py", "line_range": [136,139],
                      "excerpt": "    tax_amount = self.tax_client.get_rate(cart.region)" } ] },
    { "step": 3, "type": "hypothesise",
      "statement": "get_rate returns None under some condition.",
      "prior": 0.7 },
    { "step": 4, "type": "test",
      "statement": "Confirmed: get_rate returns None in its except branch.",
      "supports": [3],
      "evidence": [ { "kind": "file", "repo_path": "clients/tax_client.py", "line_range": [38,43],
                      "excerpt": "        except httpx.HTTPStatusError:\n            logger.warning(...)\n            return None" } ] },
    { "step": 5, "type": "test",
      "statement": "The breadcrumb shows tax-service returned 503 141 ms before the error, matching that branch.",
      "supports": [4],
      "evidence": [ { "kind": "breadcrumb", "index": 1,
                      "excerpt": "GET tax-service/rate → 503" } ] },
    { "step": 6, "type": "chain",
      "statement": "Why does get_rate swallow the error? Commit 8a3f (2026-07-25) extracted TaxClient from inline code and converted a raise into a return None, changing the contract without updating callers.",
      "evidence": [ { "kind": "commit", "sha": "8a3f...",
                      "excerpt": "refactor: extract tax lookup into TaxClient" } ] },
    { "step": 7, "type": "conclude",
      "statement": "Root cause is a broken error contract introduced by 8a3f, not the tax service outage itself. The outage is the trigger; the missing error propagation is the defect." }
  ],

  "eliminated_hypotheses": [
    { "statement": "Missing regional tax configuration",
      "eliminated_because": "The 503 breadcrumb and the except-branch return None fully explain the observation; no config lookup appears in the failing path.",
      "evidence": [ { "kind": "file", "repo_path": "clients/tax_client.py", "line_range": [20,43] } ] }
  ],

  "fix_strategy": {
    "approach": "Restore error propagation in TaxClient.get_rate and add an explicit, tested fallback policy at the call site.",
    "files_to_modify": ["clients/tax_client.py", "services/checkout.py"],
    "must_not_modify": ["api/routes/checkout.py"],
    "considerations": [
      "Do not default tax to zero silently — that under-charges customers and is worse than failing.",
      "Preserve the existing warning log for observability.",
      "The other caller services/quote.py::estimate_total has the same latent bug; note it but do not fix it in this patch (out of scope)."
    ],
    "regression_test_needed": true,
    "regression_test_description": "Assert that calculate_total raises TaxServiceUnavailable (not TypeError) when the tax client receives a 503."
  },

  "self_assessed_confidence": 0.88,
  "uncertainty_notes": [
    "The intended fallback policy when tax is unavailable is a product decision; the patch raises a typed error rather than assuming a default."
  ],
  "model": "reasoning-tier-a",
  "prompt_version": "reason.v3",
  "tokens": { "prompt": 18_412, "completion": 2_106 }
}
```

> **`self_assessed_confidence` is recorded but weighted at only 15% of the final score.** Models are systematically overconfident. Real signals — did it build, did the tests pass — dominate. See S11.

---

### S7 — `patch`

**Purpose:** Produce a minimal, correct, applicable unified diff scoped strictly to `fix_strategy.files_to_modify`.
**Model:** reasoning tier with a code-focused prompt.
**Target p95:** 15 s · **Hard timeout:** 45 s · **Token cap:** 6,000 out · **Retries:** 2 · **On scope violation:** hard fail `RT-AI-0005` (also a prompt-injection signal) · **On non-applying diff:** one retry, then `RT-AI-0006`.

#### Constraints enforced on the output

| Constraint | Enforcement |
|---|---|
| Only files in `files_to_modify` may appear | Deterministic check; violating hunks are stripped, and if the primary file is missing the stage retries |
| No file may be created outside `files_to_modify` + test paths | Deterministic |
| Diff must apply cleanly to the retrieved content | We apply it in-memory with `unidiff` before accepting |
| No unrelated reformatting | Hunk count and changed-line count are compared against a heuristic ceiling (default: ≤ 60 changed lines, ≤ 5 hunks); exceeding it flags `scope_warning` |
| Must not delete existing tests | Deterministic |
| Must not touch dependency manifests unless explicitly required | Flagged for human review if it does |
| A regression test is required when `regression_test_needed` is true | Deterministic |

#### Output contract

```jsonc
{
  "patch_id": "pat_01J2K...",
  "base_commit": "9f2b1c4e...",
  "diff": "--- a/clients/tax_client.py\n+++ b/clients/tax_client.py\n@@ -36,9 +36,12 @@\n …",
  "files_changed": [
    { "repo_path": "clients/tax_client.py", "additions": 8, "deletions": 3, "hunks": 2 },
    { "repo_path": "services/checkout.py",  "additions": 6, "deletions": 1, "hunks": 1 },
    { "repo_path": "tests/test_checkout.py","additions": 22,"deletions": 0, "hunks": 1, "is_new_test": true }
  ],
  "explanation": "TaxClient.get_rate now raises a typed TaxServiceUnavailable instead of returning None, preserving the existing warning log. calculate_total catches it and re-raises as a 503 with a clear message rather than producing a TypeError. A regression test asserts the new behaviour under a mocked 503.",
  "regression_test": {
    "repo_path": "tests/test_checkout.py",
    "test_name": "test_calculate_total_raises_when_tax_service_unavailable",
    "reproduces_original_error": true,
    "expected_before_patch": "fail",   // MUST fail on the unpatched code
    "expected_after_patch":  "pass"
  },
  "risk_assessment": {
    "level": "low",
    "breaking_change": true,
    "breaking_change_note": "get_rate's contract changes from returning Optional to raising. services/quote.py::estimate_total also calls it and will now propagate the error rather than fail with TypeError — arguably an improvement, but behaviour changes.",
    "touches_auth": false,
    "touches_data_migration": false,
    "touches_public_api": false
  },
  "alternatives_considered": [
    { "approach": "Default tax to 0 when unavailable",
      "rejected_because": "Silently under-charges customers; converts a loud failure into a financial defect." },
    { "approach": "Retry the tax call with backoff inside get_rate",
      "rejected_because": "Valuable but orthogonal; adds latency to the request path and does not fix the contract violation. Recommended as a follow-up." }
  ],
  "scope_warning": null,
  "model": "reasoning-tier-a",
  "prompt_version": "patch.v4"
}
```

---

### S8 — `validate` (sandbox)

**Purpose:** Prove the patch. This is the hard gate. Full design in `07-SANDBOX-VALIDATION.md`; the pipeline contract is here.
**Queue:** `rt:sandbox`.
**Target p95:** 45 s · **Hard kill:** 90 s (SIGKILL by the supervisor) · **Retries:** 0 — a gate failure is a *result*, not an error, and routes to S9 · **On timeout:** recorded as `failed_gate: "timeout"`, which enters the repair loop like any other failure.

> **Why the hard kill is 90 s, not 45 s (B11).** G6 runs the existing suite twice (pre-patch baseline plus post-patch) and G7 runs static analysis twice, for the same reason: only *new* findings count, which requires a before. Summing the per-gate soft budgets gives 5+3+5+5+15+8+2 = 43 s for a single pass, but the true worst case includes the second G6 (+15 s) and second G7 (+8 s) ≈ **66 s**. A 45 s kill would have terminated realistic validations mid-suite, leaving `build_passed` false and collapsing confidence to 0 — a silent, systematic failure that would have looked like poor patch quality. The 45 s figure is retained as the **p95 target**; the kill is set at 90 s so a slow-but-healthy validation is never destroyed. Per-gate soft budgets are unchanged.

#### Gate sequence (fail-fast, cheapest first)

```
G0  Diff applies cleanly                        ~5 ms    in-process
G1  Syntax parse of every changed file          ~50 ms   in-process (Tree-sitter / ast)
    ── container starts only if G0 and G1 pass ──
G2  Dependency resolution (offline cache)       ~5 s
G3  Import / compile check                      ~3 s
G4  Regression test — pre-patch                 ~5 s     MUST FAIL (proves the test is real)
G5  Regression test — post-patch                ~5 s     MUST PASS
G6  Existing test suite (scoped)                ~15 s    MUST NOT REGRESS
G7  Static analysis (ruff, mypy, bandit)        ~8 s     no new HIGH findings
G8  Security scan of the diff                   ~2 s     no new dangerous constructs
```

**G4 is the step most systems skip and it is the one that matters most.** If the AI's regression test *passes* on the unpatched code, the test does not actually reproduce the bug — it is theatre. Requiring it to fail first turns the test from decoration into evidence.

#### Output contract

```jsonc
{
  "validation_id": "val_01J2K...",
  "attempt": 1,
  "passed": true,
  "gates": [
    { "gate": "G0_diff_applies",     "passed": true,  "duration_ms": 4 },
    { "gate": "G1_syntax",           "passed": true,  "duration_ms": 47,
      "detail": { "files_parsed": 3 } },
    { "gate": "G2_dependencies",     "passed": true,  "duration_ms": 4_820 },
    { "gate": "G3_compile",          "passed": true,  "duration_ms": 2_940 },
    { "gate": "G4_regression_pre",   "passed": true,  "duration_ms": 5_110,
      "detail": { "expected": "fail", "actual": "fail", "assertion": "TypeError raised as expected on unpatched code" } },
    { "gate": "G5_regression_post",  "passed": true,  "duration_ms": 4_802,
      "detail": { "expected": "pass", "actual": "pass" } },
    { "gate": "G6_existing_tests",   "passed": true,  "duration_ms": 14_203,
      "detail": { "total": 47, "passed": 47, "failed": 0, "skipped": 2,
                  "newly_failing": [] } },
    { "gate": "G7_static_analysis",  "passed": true,  "duration_ms": 7_400,
      "detail": { "ruff": { "new": 0 }, "mypy": { "new": 0 }, "bandit": { "new_high": 0, "new_medium": 1 } } },
    { "gate": "G8_security_scan",    "passed": true,  "duration_ms": 1_900,
      "detail": { "findings": [] } }
  ],
  "failed_gate": null,
  "transcript_url": "s3://roottrace-logs/sandbox/val_01J2K.../transcript.log",
  "resource_usage": { "wall_ms": 41_226, "cpu_ms": 28_940, "peak_memory_mb": 412, "container_id": "sbx_…" },
  "signals_for_scoring": {
    "build_passed": true,
    "regression_test_valid": true,       // G4 failed pre-patch → the test is real
    "test_pass_ratio": 1.0,
    "new_static_findings_high": 0,
    "new_static_findings_medium": 1
  }
}
```

#### Failure output (feeds S9)

```jsonc
{
  "passed": false,
  "failed_gate": "G6_existing_tests",
  "failure_detail": {
    "newly_failing": [
      { "test": "tests/test_quote.py::test_estimate_with_missing_tax",
        "error": "TaxServiceUnavailable: tax service returned 503",
        "traceback": "…",
        "note": "This test asserted the old None-returning behaviour." }
    ]
  },
  "repair_hint": "The patch changed get_rate's contract. tests/test_quote.py::test_estimate_with_missing_tax depends on the old behaviour. Either update that test to reflect the corrected contract, or scope the change so quote.py's path is unaffected."
}
```

---

### S9 — `repair`

**Purpose:** Turn a validation failure into a better attempt. This loop is what converts a ~60% first-attempt success rate into ~85%.
**Target p95:** 2 s · **Hard timeout:** 5 s · **Max attempts:** 3 (routing decisions only; the cost is in the S6/S7 re-entry) · **On exhaustion:** terminal `validation_failed`, every attempt retained and inspectable.

#### Algorithm

```
attempt = validation.attempt
if attempt >= max_attempts (3):
    → terminal state validation_failed, preserve every attempt for inspection

repair_context = {
    original ErrorUnderstanding (S4),
    original RootCause (S6),
    the failed patch (S7),
    the full sandbox transcript (S8) — stderr verbatim, not summarised,
    every previous attempt and why each failed
}

Route by failed gate:
    G1 syntax          → targeted syntax-fix prompt, cheap model, no re-reasoning
    G2 dependencies    → the patch introduced an unavailable import; instruct to use
                         only modules already present in the retrieved context
    G3 compile         → type/import error; supply the compiler output verbatim
    G4 regression_pre  → the test does not reproduce the bug. Regenerate the TEST
                         only, not the fix. This is a test-quality failure.
    G5 regression_post → the fix does not actually fix it. Return to S6 reasoning
                         with the failure as new evidence — the root cause was wrong.
    G6 existing_tests  → regression introduced. Show which tests broke and how;
                         instruct to preserve existing contracts or update the tests
                         with justification.
    G7/G8 static/sec   → targeted remediation of the specific findings only.

→ re-enter S7 (or S6 for the G5 case) with repair_context
```

The G5 routing is important and easy to get wrong: if the fix doesn't fix it, patching harder is futile — the *diagnosis* was wrong. Only a G5 failure sends the pipeline back to reasoning.

#### Output

```jsonc
{
  "repair_id": "rep_01J...",
  "attempt": 2,
  "failed_gate": "G6_existing_tests",
  "strategy": "preserve_existing_contract",
  "reroute_to_stage": "S7",
  "instruction_delta": "Keep the typed exception, but update tests/test_quote.py to assert the new behaviour, and state in the PR description that quote.py's error surface changed.",
  "previous_attempts_summary": [
    { "attempt": 1, "failed_gate": "G6_existing_tests",
      "reason": "broke tests/test_quote.py::test_estimate_with_missing_tax" }
  ]
}
```

---

### S10 — `critique`

**Purpose:** An independent adversarial review. Separate call, **fresh context**, critic persona.
**Model:** reasoning tier (ideally a different provider than S6/S7 — the separation is the point).
**Target p95:** 12 s · **Hard timeout:** 30 s · **Token cap:** 2,500 out · **Retries:** 2 · **On exhaustion:** proceed to S11 with `critic_component = 0` and a visible "review unavailable" banner — never silently treated as approval.

#### Why a separate call, not a "check your work" instruction

A model asked to critique its own output in the same context is anchored on its prior reasoning and reliably approves it. Giving a fresh context the diff, the original error, and the sandbox results — *without* the reasoning that produced them — makes the review genuinely independent. This is the difference between a real review and rubber-stamping.

The critic sees:
- the original error and stack trace
- the retrieved context bundle
- the final diff
- the sandbox results

The critic does **not** see:
- S6's reasoning chain
- S7's explanation or self-assessment

#### Review dimensions

| Dimension | Question |
|---|---|
| Correctness | Does this diff actually address the stack trace, or does it mask the symptom? |
| Completeness | Are there other call sites with the same defect that are silently left broken? |
| Regression risk | What existing behaviour changes? Who depends on it? |
| Security | Injection, auth bypass, information disclosure, unsafe deserialisation, secrets in code |
| Scope | Is anything here unrelated to the reported error? |
| Test quality | Does the regression test genuinely reproduce the bug, or does it assert something trivially true? |
| Style | Does it match the surrounding code's conventions? |

#### Output contract

```jsonc
{
  "verdict": "approve_with_notes",   // approve | approve_with_notes | request_changes | reject
  "agreement_with_diagnosis": 0.9,
  "addresses_reported_error": true,
  "findings": [
    { "severity": "medium", "dimension": "completeness",
      "statement": "services/quote.py::estimate_total calls the same get_rate and will now propagate TaxServiceUnavailable to an unprepared caller.",
      "evidence": { "repo_path": "services/quote.py", "line_range": [31,36] },
      "recommendation": "Acceptable to leave out of scope, but the PR description must state it explicitly so a reviewer isn't surprised." },
    { "severity": "low", "dimension": "style",
      "statement": "The new exception class is defined inline in tax_client.py while sibling exceptions live in errors.py.",
      "recommendation": "Move TaxServiceUnavailable to clients/errors.py for consistency." }
  ],
  "security_review": { "concerns": [], "clean": true },
  "regression_risk": "low",
  "test_quality": { "reproduces_bug": true, "assessment": "The test mocks a 503 and asserts the typed exception; it is a genuine reproduction, not a tautology." },
  "scope_assessment": "Tightly scoped. No unrelated changes.",
  "blocking": false,
  "model": "reasoning-tier-b",
  "prompt_version": "critique.v2"
}
```

A `reject` verdict, or any `severity: critical` finding, is **blocking** — the investigation terminates as `low_confidence` regardless of sandbox results, and the critique is shown prominently.

---

### S11 — `score`

**Purpose:** Compute a single, defensible confidence number from real signals, with a breakdown the user can interrogate.
**Target p95:** 200 ms · **Hard timeout:** 1 s · **Retries:** 3 · **On exhaustion:** terminal `failed` — we never publish without a score. Pure computation, no LLM.

#### The formula

```
confidence =
    0.30 × validation_component
  + 0.20 × critic_component
  + 0.15 × retrieval_component
  + 0.15 × evidence_component
  + 0.10 × model_self_assessment
  + 0.10 × historical_component
```

**`validation_component` (0.30)** — the largest weight, because it is the only signal grounded in execution rather than opinion.

```
  build_passed              ? 0.30 : 0.00      (hard gate; if 0, whole score is 0)
+ regression_test_valid     ? 0.25 : 0.00      (G4 failed pre-patch)
+ test_pass_ratio           × 0.25
+ (no new HIGH static)      ? 0.10 : 0.00
+ (no new MEDIUM static)    ? 0.10 : 0.05
+ first_attempt_pass        ? 0.00 : −0.05 × (attempts − 1)
```

**`critic_component` (0.20)**

```
approve = 1.00 | approve_with_notes = 0.80 | request_changes = 0.35 | reject = 0.00
× agreement_with_diagnosis
− 0.15 per HIGH finding, − 0.05 per MEDIUM finding    (floor 0)
```

**`retrieval_component` (0.15)** — `bundle.quality.score` from S5, penalised 0.05 per entry in `gaps`.

**`evidence_component` (0.15)**

```
  fraction of findings that survived evidence validation      × 0.50
+ (root cause cites the failure-point file)         ? 0.20 : 0
+ (a blame/release correlation was found)           ? 0.15 : 0
+ (reasoning chain reached a code-actionable cause) ? 0.15 : 0
```

**`model_self_assessment` (0.10)** — S6's `self_assessed_confidence`, deliberately capped low.

**`historical_component` (0.10)** — V1 returns a constant 0.5 (no history yet). From V3, this is the project's observed merge rate for patches of this `root_cause.category`, where the V3 feedback loop actually pays off.

#### Hard gates that force a low outcome regardless of arithmetic

| Condition | Result |
|---|---|
| `build_passed = false` | `confidence = 0`, never published |
| Critic verdict `reject` | `confidence = min(confidence, 0.25)`, never published |
| Any critical security finding | `confidence = 0`, never published |
| `regression_test_valid = false` | `confidence = min(confidence, 0.50)` |
| `retrieval.quality.score < 0.4` | `confidence = min(confidence, 0.45)` |

#### Bands

| Band | Range | Meaning | Default routing |
|---|---|---|---|
| **High** | ≥ 0.80 | Strong evidence, clean validation, critic approval | PR opened, auto-merge eligible if enabled |
| **Medium** | 0.60–0.79 | Sound but with caveats | PR opened, human review required |
| **Low** | 0.40–0.59 | Plausible, weak support | PR opened as **draft**, flagged |
| **Insufficient** | < 0.40 | Not credible | No PR. Shown in dashboard as analysis-only |

#### Output contract

```jsonc
{
  "confidence": 0.836,
  "band": "high",
  "breakdown": {
    "validation":      { "weight": 0.30, "raw": 0.95, "contribution": 0.285 },
    "critic":          { "weight": 0.20, "raw": 0.67, "contribution": 0.134 },
    "retrieval":       { "weight": 0.15, "raw": 0.86, "contribution": 0.129 },
    "evidence":        { "weight": 0.15, "raw": 1.00, "contribution": 0.150 },
    "self_assessment": { "weight": 0.10, "raw": 0.88, "contribution": 0.088 },
    "historical":      { "weight": 0.10, "raw": 0.50, "contribution": 0.050 }
  },
  "gates_applied": [],
  "explanation": "High confidence. The patch built cleanly, the regression test correctly failed before the fix and passed after, all 47 existing tests still pass, and the independent reviewer approved with two non-blocking notes. Retrieval resolved the failure point, the entry point, and the introducing commit. One retrieval gap (get_regional_config) was not on the failing path.",
  "should_publish": true,
  "publish_mode": "open_pr",       // open_pr | open_draft_pr | analysis_only
  "auto_merge_eligible": false      // requires repo opt-in AND path match AND band=high
}
```

---

### S12 — `publish`

**Purpose:** Create the branch, commit, and pull request. **Never a clone, never a working directory** — everything through the Git Data API.
**Queue:** `rt:github`.
**Target p95:** 4 s · **Hard timeout:** 20 s · **Retries:** 3, exponential backoff · **On exhaustion:** terminal `failed`; the investigation and all artefacts are retained, and publish is separately replayable without re-running the pipeline.

Full mechanics in `08-GITHUB-INTEGRATION.md`. Sequence:

```
1. Mint a fresh installation token (60-min lifetime, never persisted)
2. GET  /repos/{o}/{r}/git/ref/heads/{default}   → base SHA
3. POST /repos/{o}/{r}/git/blobs                 → one blob per changed file
4. POST /repos/{o}/{r}/git/trees                 → tree with base_tree = base SHA
5. POST /repos/{o}/{r}/git/commits               → commit (author: RootTrace AI bot,
                                                     co-author trailer if configured)
6. POST /repos/{o}/{r}/git/refs                  → refs/heads/roottrace/fix-<fp8>
7. POST /repos/{o}/{r}/pulls                     → PR (draft if band = low)
8. POST /issues/{n}/labels                       → roottrace, confidence:high, severity:P1
9. INSERT pull_request_records
```

#### PR description template

```markdown
## 🔍 Root cause

TaxClient.get_rate() catches HTTPError and returns None on any non-200 response,
so a 503 from the tax service silently yields None, which calculate_total() then
adds to a Decimal.

**Mechanism:** tax-service 503 → `TaxClient.get_rate` catches `httpx.HTTPStatusError`
(`clients/tax_client.py:41`) and returns `None` → `calculate_total` receives `None`
(`services/checkout.py:138`) with no guard → line 142 evaluates `Decimal + None`.

**Introduced by** [`8a3f1c2`](../../commit/8a3f1c2) — *"refactor: extract tax lookup
into TaxClient"* (dana@acme.io, 2026-07-25) — which replaced a `raise` with a
`return None`, changing the contract without updating callers.

## 📋 Evidence

| # | Source | What it shows |
|---|---|---|
| 1 | [`services/checkout.py:140-143`](../../blob/9f2b1c4/services/checkout.py#L140-L143) | The failing addition |
| 2 | [`clients/tax_client.py:38-43`](../../blob/9f2b1c4/clients/tax_client.py#L38-L43) | The `except` branch returning `None` |
| 3 | Breadcrumb, T−141 ms | `GET tax-service/rate → 503` |
| 4 | Commit `8a3f1c2` | The contract change |

## ✅ Validation — all gates passed

| Gate | Result |
|---|---|
| Diff applies | ✅ |
| Syntax | ✅ 3 files |
| Dependencies | ✅ |
| Compile | ✅ |
| **Regression test fails on unpatched code** | ✅ *(proves the test is real)* |
| **Regression test passes on patched code** | ✅ |
| Existing suite | ✅ 47/47, 0 newly failing |
| Static analysis | ✅ 0 new high, 1 new medium |
| Security scan | ✅ clean |

Validated in RootTrace's isolated sandbox — no network, no credentials.
[Full transcript →](https://app.roottrace.ai/i/inv_01J2K/sandbox)

## 🤖 Independent review — approve with notes

- **medium / completeness** — `services/quote.py::estimate_total` calls the same
  `get_rate` and will now propagate `TaxServiceUnavailable`. Left out of scope
  deliberately; flagging so it isn't a surprise.
- **low / style** — `TaxServiceUnavailable` is defined inline; siblings live in
  `clients/errors.py`.

Security review: clean. Regression risk: low.

## 📊 Confidence — 0.84 (high)

| Component | Weight | Score |
|---|---|---|
| Sandbox validation | 30% | 0.95 |
| Independent review | 20% | 0.67 |
| Retrieval quality | 15% | 0.86 |
| Evidence binding | 15% | 1.00 |
| Model self-assessment | 10% | 0.88 |
| Historical accuracy | 10% | 0.50 |

## ⚠️ Considered and rejected

- **Default tax to 0 when unavailable** — silently under-charges customers; turns a
  loud failure into a financial defect.
- **Retry with backoff inside `get_rate`** — worth doing, but orthogonal; doesn't fix
  the contract violation. Recommended as a follow-up.

## 🔗 Links

- [Full investigation](https://app.roottrace.ai/i/inv_01J2K)
- [Issue — 1,247 occurrences since 2026-07-28](https://app.roottrace.ai/issues/iss_01J2K)

---
<sub>Opened by **RootTrace AI**. Every claim above links to its source. This patch was
compiled and tested before this PR was created.</sub>
```

---

### S13 — `await_decision`

**Purpose:** Track what the human actually does. Event-driven, not polling.
**Trigger:** GitHub webhooks — `pull_request`, `pull_request_review`, `push` to the PR branch.

| Event | Recorded outcome |
|---|---|
| PR merged, zero commits added after ours | `merged_unchanged` — the strongest positive signal available |
| PR merged, commits added after ours | `edited_and_merged` — we capture the diff between our commit and the merge head; **this is the most valuable training signal in the system** |
| PR closed unmerged | `rejected` — a comment-request prompt asks why, optionally |
| No action for 7 days | `stale` |
| Branch force-pushed by a human | `human_took_over` |

For `edited_and_merged`, we compute and store `human_edit_diff`. It answers the question that matters most: *what did we get wrong, specifically?*

---

### S14 — `feedback`

**Purpose:** Persist the outcome as structured signal. In V1 this only records; from **V3** it feeds retrieval weighting, the historical confidence component, and prompt selection.
**Target p95:** 3 s · **Hard timeout:** 10 s · **Token cap:** 1,000 out · **Retries:** 2 · **On exhaustion:** the raw outcome is still persisted; only `edit_analysis` is left null.

```jsonc
{
  "feedback_id": "fb_01J...",
  "investigation_id": "inv_01J2K...",
  "outcome": "edited_and_merged",
  "decided_at": "2026-08-04T11:42:00Z",
  "decided_by": "priya@acme.io",
  "time_to_decision_seconds": 8_940,
  "human_edit_diff": "…",
  "edit_analysis": {
    "our_lines_kept": 12,
    "our_lines_modified": 2,
    "our_lines_removed": 0,
    "lines_added_by_human": 5,
    "semantic_verdict": "diagnosis_correct_implementation_refined",
    "note": "Human moved TaxServiceUnavailable to clients/errors.py — exactly the critic's low-severity note. Core fix retained unchanged."
  },
  "signal_strength": 0.85,
  "learning_targets": ["patch_style_conventions", "exception_placement_convention"]
}
```

`semantic_verdict` taxonomy, in descending value:

| Verdict | Meaning |
|---|---|
| `fully_correct` | Merged unchanged |
| `diagnosis_correct_implementation_refined` | We found the bug; the human polished the fix |
| `diagnosis_correct_scope_expanded` | We found it; the human fixed more |
| `diagnosis_partially_correct` | Right area, wrong mechanism |
| `diagnosis_incorrect` | Wrong root cause; the human fixed something else |
| `rejected_low_value` | Correct but not worth merging |
| `rejected_style` | Correct but violates conventions |

---

## 5. Orchestrator implementation

```python
STAGES: list[type[Stage]] = [
    UnderstandStage,   # S4
    RetrieveStage,     # S5
    ReasonStage,       # S6
    PatchStage,        # S7
    ValidateStage,     # S8   (may loop back to S7/S6 via RepairStage)
    CritiqueStage,     # S10
    ScoreStage,        # S11
    PublishStage,      # S12
]

async def run_pipeline(investigation_id: UUID, *, resume: bool = False) -> None:
    inv = await load_investigation(investigation_id)
    ctx = PipelineContext(investigation=inv)

    if resume:
        ctx = await rehydrate_from_completed_steps(inv)   # R3 resumability

    for stage in STAGES:
        if await stage_already_completed(inv.id, stage.name):   # R2 idempotency
            ctx = await load_stage_output_into(ctx, stage.name)
            continue

        step = await begin_step(inv.id, stage.name)
        await publish_ws(inv, stage.name, "running")

        try:
            async with timeout(stage.timeout_seconds):
                output = await stage.execute(ctx)

            stage.output_model.model_validate(output)         # R4 contract
            ctx = ctx.merge(stage.name, output)
            await complete_step(step, output, tokens=..., cost_micro_usd=...)
            await publish_ws(inv, stage.name, "completed", summary=stage.summarize(output))

        except StageTerminal as t:                            # honest terminal state
            await terminate(inv, t.state, t.reason)
            await publish_ws(inv, stage.name, "terminal", reason=t.reason)
            return

        except StageRetryable as r:
            await fail_step(step, r); raise                   # ARQ redelivers

        except Exception as e:
            await fail_step(step, e)
            await terminate(inv, "failed", str(e))
            await publish_ws(inv, stage.name, "failed", error=str(e))
            return

        # the validate→repair loop is expressed inside ValidateStage,
        # which re-enqueues S7/S6 and returns StageDeferred
```

---

## 6. Stage budget reference — CANONICAL

> **This table is the single source of truth for every stage timing value.** `02` §9, `12` §8, `A3` §1, and the `RT_PIPELINE_STAGE_TIMEOUT_SECONDS` default all derive from it and must not restate different numbers. Registered in `18` §4.

**Target p95** and **hard timeout** are different things and are never used interchangeably:

- **Target p95** — what we expect and what we alert on. Exceeding it means *slow*, and it burns SLO budget.
- **Hard timeout** — the kill limit, set deliberately above the target so a slow-but-healthy call is not destroyed. Exceeding it means *failed*.

| Stage | Queue | Target p95 | Hard timeout | Retries | Token cap | Tier | Cost | On exhaustion |
|---|---|---|---|---|---|---|---|---|
| S1 receive | api | 50 ms | 2 s | 0 | — | — | $0 | 5xx; nothing persisted |
| S2 fingerprint | rt:ingest | 100 ms | 1 s | 3 | — | — | $0 | dead-letter |
| S3 triage | rt:ingest | 200 ms | 1 s | 3 | — | — | $0 | dead-letter |
| S4 understand | rt:pipeline | 3 s | 10 s | 2 | 2k out | fast | ~$0.004 | degrade to pre-parse, continue |
| S5 retrieve | rt:pipeline | 8 s | 20 s | 2 | 24k ctx | embed | ~$0.002 | terminal `insufficient_context` |
| S6 reason | rt:pipeline | 25 s | 60 s | 2 | 4k out | reasoning-a | ~$0.140 | terminal `insufficient_context` |
| S7 patch | rt:pipeline | 15 s | 45 s | 2 | 6k out | reasoning-a | ~$0.090 | terminal `failed` (`RT-AI-0005/0006`) |
| S8 validate | rt:sandbox | **45 s** | **90 s** | 0 | — | — | ~$0.003 | timeout → repair loop |
| S9 repair | rt:pipeline | 2 s | 5 s | 1 | 1k out | fast | ~$0.002 | terminal `validation_failed` |
| S10 critique | rt:pipeline | 12 s | 30 s | 2 | 2.5k out | reasoning-b | ~$0.070 | continue, `critic_component = 0` |
| S11 score | rt:pipeline | 200 ms | 1 s | 3 | — | — | $0 | terminal `failed` |
| S12 publish | rt:github | 4 s | 20 s | 3 | 1k out | fast | ~$0.008 | terminal `failed`, replayable |
| S13 await | webhook | — | 7 d → `stale` | — | — | — | $0 | `stale` |
| S14 feedback | rt:pipeline | 3 s | 10 s | 2 | 1k out | fast | ~$0.003 | outcome kept, analysis null |

**End-to-end totals**

| Path | Target p95 | Worst case (all hard timeouts) | Cost |
|---|---|---|---|
| Happy path | **≈ 115 s** | 300 s | ≈ $0.32 |
| One repair cycle | **≈ 175 s** | 460 s | ≈ $0.42 |
| Three repair cycles (max) | ≈ 295 s | 780 s | ≈ $0.62 |

The pipeline p95 SLO in `12` §8 is **240 s**, which accommodates the happy path plus one repair with headroom. The worst-case column is not an SLO — it is the bound used to size queue visibility timeouts and the stuck-investigation reaper.

---

## 7. WebSocket event contract

Every stage transition emits exactly one frame on `ws://…/v1/investigations/{id}/stream`:

```jsonc
{
  "type": "stage_update",
  "investigation_id": "inv_01J2K...",
  "sequence": 7,
  "stage": "validate",
  "status": "completed",            // queued|running|completed|failed|terminal|deferred
  "started_at":  "2026-08-04T09:15:02.100Z",
  "completed_at":"2026-08-04T09:15:43.326Z",
  "duration_ms": 41_226,
  "summary": "All 9 gates passed. 47/47 tests green.",
  "metrics": { "tokens_in": 0, "tokens_out": 0, "cost_micro_usd": 3_000 },
  "progress": { "current_stage": 8, "total_stages": 14 },
  "investigation_status": "reviewing"
}
```

Additional frame types: `log_line` (streaming sandbox stdout), `investigation_status_change`, `error`.

---

## 8. Stage contract register — CANONICAL

The 14 stages, each with its full contract. **No stage may perform another stage's responsibility.** Where a stage looks like it is doing neighbouring work, the boundary is stated explicitly.

Timing columns are omitted here — §6 is canonical for those. Full input/output JSON schemas are in each stage's section above.

| # | Stage | Input | Output | Idempotency key | Security requirement | Observability |
|---|---|---|---|---|---|---|
| S1 | `receive` | HTTP batch ≤100 events | `raw_events` rows + 202 envelope | `Idempotency-Key` (Redis `SET NX`) | Ingest key is **write-only**; sanitise before persist; header allowlist | `rt_events_received_total`, `rt_ingest_duration_seconds` |
| S2 | `fingerprint` | one `raw_event` | `issues` upsert + `error_occurrences` row | `(project_id, fingerprint)` unique | Message normalisation must not leak values into the hash input | `rt_events_*`, issue upsert rate |
| S3 | `triage` | occurrence + issue counters | severity + `investigations` row or gate reason | `investigations_one_active_per_issue` partial unique | Quota and cost gates evaluated **before** any spend | `rt_investigations_started_total` |
| S4 | `understand` | error + breadcrumbs + request | `ErrorUnderstanding` + retrieval plan | `pipeline_steps(inv, 'understand', attempt)` | Payload is untrusted (L4 fencing); no repo access yet | `rt_llm_*` tagged `stage=understand` |
| S5 | `retrieve` | retrieval plan | `ContextBundle` ≤24k tokens | same, per stage+attempt | **P3 budget is hard**; retrieved source is untrusted; never clone | `strategy_stats`, `quality.score` |
| S6 | `reason` | `ContextBundle` | `RootCauseAnalysis` with bound evidence | same | **H1/H2 evidence binding**; unbound findings discarded | `rt_evidence_validation_failures_total` |
| S7 | `patch` | root cause + fix strategy | unified diff + regression test | same | **H6 scope enforcement**; forbidden-path allowlist; H5 applicability | `rt_llm_*`, scope-warning rate |
| S8 | `validate` | diff + original files + manifest | `ValidationRun`, 9 gates G0–G8 | `validation_runs(inv, attempt)` unique | Full sandbox isolation L1–L8; no network, no credentials | `rt_sandbox_*`, per-gate results |
| S9 | `repair` | failed `ValidationRun` | routing decision + instruction delta | `repair_attempts` counter on the investigation | Verbatim stderr is untrusted content | `rt_repair_attempts_total{failed_gate}` |
| S10 | `critique` | error + bundle + diff + gate results | `Critique` verdict | `critiques(inv)` unique | **Fresh context**: must NOT receive S6 chain or S7 explanation | `rt_llm_*` tagged `tier=reasoning-b` |
| S11 | `score` | all prior artefacts | `ConfidenceScore` + publish mode | `confidence_scores(inv)` unique | Hard gates cannot be bypassed by arithmetic | `rt_confidence_distribution` |
| S12 | `publish` | patch + score + critique | branch, commit, PR record | branch name derived from fingerprint; PR record unique per inv | Installation token minted per operation, never persisted | `rt_prs_opened_total` |
| S13 | `await_decision` | GitHub webhook | outcome classification | `X-GitHub-Delivery` replay guard | HMAC verified constant-time before any processing | `rt_github_webhook_received_total` |
| S14 | `feedback` | decision + edit diff | `FeedbackEvent` | `feedback_events(inv, outcome, decided_at)` | Human edit diff is untrusted content | `rt_merge_rate`, `rt_false_confidence_rate` |

### 8.1 Boundaries that are easy to violate

| Temptation | Why it is wrong | Correct owner |
|---|---|---|
| S4 fetching a file to "check" a path | S4 has no repo access by design; it produces a *plan* | S5 |
| S5 forming a hypothesis while ranking | Ranking must stay mechanical and explainable | S6 |
| S6 writing code in `mechanism` | Root cause is prose + evidence; code is a diff | S7 |
| S7 deciding whether the patch is good | Self-assessment is not validation | S8, S10 |
| S8 deciding what to do about a failure | Gates report; routing is a separate decision | S9 |
| S10 seeing S6's reasoning | Destroys critic independence — the entire value of the stage | — |
| S11 re-running anything | Scoring is pure computation over existing artefacts | — |
| S12 re-validating before publishing | Publishing is gated on the score, not on a second opinion | S11 |

### 8.2 Universal stage invariants

Every stage, without exception:

1. Writes exactly **one** `pipeline_steps` row (`unique (investigation_id, stage, attempt)`).
2. Publishes exactly **one** WebSocket frame per status transition.
3. Validates its output against a Pydantic model before returning (**R4**).
4. Checks whether its output already exists before doing work (**R2**).
5. Records tokens, cost, and duration — zero is recorded explicitly, never omitted.
6. Can terminate the run with an honest terminal state (**R7**).

---

*Next: [`04-DATA-MODEL.md`](./04-DATA-MODEL.md)*
