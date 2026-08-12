# 17 — Glossary & Error Code Registry

---

## 1. Domain terms

| Term | Definition |
|---|---|
| **Blast radius** | The set of endpoints, functions, and callers affected by a root cause. Determines severity justification and warns about out-of-scope callers with the same latent defect |
| **Breadcrumb** | A recorded event immediately preceding an error (DB query, HTTP call, log line). Disproportionately valuable — often contains the actual cause when the stack trace only shows the symptom |
| **Confidence band** | `high` (≥0.80), `medium` (0.60–0.79), `low` (0.40–0.59), `insufficient` (<0.40). Determines whether and how a PR is published |
| **Context bundle** | The assembled set of source files, graph, history, and tests passed to the reasoning stage. Hard-capped at 24,000 tokens |
| **Critic** | The independent review model. Separate call, fresh context, never sees the reasoning that produced the patch |
| **Culprit** | The `file::function` where the error surfaced. Distinct from the root cause, which may be elsewhere |
| **Degraded mode** | Sandbox validation that could not run every gate (usually a dependency cache miss). Always reported honestly; caps confidence |
| **Evidence binding** | The requirement that every AI claim cite a real artefact from the context bundle, verified by literal comparison. The primary anti-hallucination mechanism |
| **Fingerprint** | Deterministic 32-char hash grouping identical errors into one Issue. Stable across line-number changes; distinguishes genuinely different code paths |
| **Gate** | A pass/fail check in sandbox validation (G0–G8). Failing a hard gate blocks publication |
| **Ground truth** | The known-correct answer for a fixture error case, used to measure accuracy in the evaluation harness |
| **In-app frame** | A stack frame in the customer's own code, as opposed to stdlib, vendor, `site-packages`, or `node_modules` |
| **Investigation** | One run of the 14-stage pipeline against one issue |
| **Issue** | A group of errors sharing a fingerprint. The unit users manage |
| **Occurrence** | A single instance of an error. Many occurrences map to one issue |
| **Repair loop** | The bounded cycle (max 3) of feeding sandbox failures back into patch generation, or into reasoning when the fix didn't fix it |
| **Retrieval quality score** | 0–1 measure of how well context assembly succeeded. Feeds directly into final confidence |
| **Root cause** | The code-actionable defect, not the symptom and not the trigger. "The client swallows 5xx," not "the service was down" |
| **Scope enforcement** | Deterministic rejection of any patch touching a file outside the fix strategy. A key prompt-injection defence |
| **Selective retrieval** | Fetching only the files a stack trace and call graph justify. Never a clone. Our cost, latency, accuracy, and security strategy at once |
| **Terminal state** | A final investigation status. Includes honest non-answers: `insufficient_context`, `validation_failed`, `low_confidence` |
| **Untrusted context** | Any content originating from customer data — source, error messages, commit messages. Fenced and never treated as instructions |

---

## 2. Pipeline stages

| # | Stage | One line |
|---|---|---|
| S1 | `receive` | Authenticate, validate, sanitise, persist, enqueue |
| S2 | `fingerprint` | Group into an issue by deterministic hash |
| S3 | `triage` | Score severity; decide whether to investigate |
| S4 | `understand` | Structure the error; produce a retrieval plan |
| S5 | `retrieve` | Assemble minimum sufficient code context |
| S6 | `reason` | Determine root cause with bound evidence |
| S7 | `patch` | Generate a scoped unified diff |
| S8 | `validate` | Prove it in the sandbox — 9 gates |
| S9 | `repair` | Route failures back for another attempt |
| S10 | `critique` | Independent adversarial review |
| S11 | `score` | Composite confidence from real signals |
| S12 | `publish` | Branch, commit, PR via Git Data API |
| S13 | `await_decision` | Track what the human does |
| S14 | `feedback` | Persist the outcome as structured signal |

---

## 3. Sandbox gates

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

G4 is the gate most systems omit and the one that matters most: a test that passes both before and after proves nothing.

---

## 4. Error code registry

Format: `RT-<DOMAIN>-<NNNN>`

### Authentication & authorisation

| Code | HTTP | Meaning |
|---|---|---|
| `RT-AUTH-0001` | 401 | Missing or malformed credential |
| `RT-AUTH-0002` | 401 | Expired token |
| `RT-AUTH-0003` | 403 | Insufficient scope |
| `RT-AUTH-0004` | 401 | Key revoked |
| `RT-AUTH-0005` | 403 | No access to the requested project |
| `RT-AUTH-0006` | 403 | Insufficient role for this action |
| `RT-AUTH-0007` | 401 | Refresh token reuse detected — family revoked (GoTrue) |
| `RT-AUTH-0008` | 401 | JWT signature invalid or `kid` unresolvable against JWKS |
| `RT-AUTH-0020` | 401 | Webhook signature verification failed |
| `RT-AUTH-0030` | 409 | Last owner cannot be removed from an organization or project |
| `RT-AUTH-0031` | 403 | Membership modification requires owner role |

### Ingestion

| Code | HTTP | Meaning |
|---|---|---|
| `RT-INGEST-0003` | 400 | Batch exceeds 100 events |
| `RT-INGEST-0004` | 413 | Payload exceeds 5 MB |
| `RT-INGEST-0010` | 422 | All events in batch invalid |
| `RT-INGEST-0011` | 422 | Required event field missing |
| `RT-INGEST-0012` | 422 | Timestamp outside the accepted window |
| `RT-INGEST-0013` | 422 | Unknown environment value |
| `RT-INGEST-0014` | 422 | Malformed stack trace |

### Validation, rate, quota

| Code | HTTP | Meaning |
|---|---|---|
| `RT-VALIDATION-0001` | 422 | Request schema validation failed |
| `RT-VALIDATION-0002` | 405 | HTTP method not allowed for this path |
| `RT-RATE-0001` | 429 | Rate limit exceeded |
| `RT-RATE-0002` | 429 | Concurrent WebSocket limit reached |
| `RT-QUOTA-0001` | 402 | Project quota exhausted |
| `RT-QUOTA-0002` | 402 | Cost circuit breaker open |

### Resources

| Code | HTTP | Meaning |
|---|---|---|
| `RT-NOTFOUND-0001` | 404 | Resource not found or not accessible |
| `RT-NOTFOUND-0002` | 404 | Source event expired; investigation is no longer replayable |
| `RT-CONFLICT-0001` | 409 | Idempotency key reused with a different body |
| `RT-CONFLICT-0002` | 409 | Investigation already running for this issue |
| `RT-CONFLICT-0003` | 409 | Repository already connected to another project |
| `RT-CONFLICT-0004` | 409 | Idempotency key claimed by an in-flight duplicate request |

### Pipeline

| Code | HTTP | Meaning |
|---|---|---|
| `RT-PIPELINE-0001` | 500 | Stage failed unrecoverably |
| `RT-PIPELINE-0002` | — | Terminal: insufficient context |
| `RT-PIPELINE-0003` | — | Terminal: validation failed after max attempts |
| `RT-PIPELINE-0004` | — | Terminal: confidence below publish floor |
| `RT-PIPELINE-0005` | — | Terminal: critic rejected |
| `RT-PIPELINE-0006` | — | Cancelled by user |
| `RT-PIPELINE-0007` | 504 | Stage timed out |
| `RT-PIPELINE-0008` | 500 | Stage output failed contract validation |

### AI

| Code | HTTP | Meaning |
|---|---|---|
| `RT-AI-0001` | 502 | All providers in tier unavailable |
| `RT-AI-0002` | 502 | Provider rate limit, retries exhausted |
| `RT-AI-0003` | 500 | Structured output unparseable after repair |
| `RT-AI-0004` | 500 | Evidence binding failed after retry |
| `RT-AI-0005` | 500 | Patch scope violation after retry |
| `RT-AI-0006` | 500 | Diff does not apply after retry |
| `RT-AI-0007` | 400 | Suspicious content detected; call aborted |

### GitHub

| Code | HTTP | Meaning |
|---|---|---|
| `RT-GITHUB-0001` | 502 | GitHub API error |
| `RT-GITHUB-0002` | 403 | App lacks required permission |
| `RT-GITHUB-0003` | 409 | Branch already exists |
| `RT-GITHUB-0004` | 404 | File not found at the requested ref |
| `RT-GITHUB-0005` | 403 | Rate limit exhausted |
| `RT-GITHUB-0006` | 422 | PR creation failed (no diff between branches) |
| `RT-GITHUB-0007` | 403 | Branch protection prevents the push |
| `RT-GITHUB-0008` | 401 | Installation requires re-authorisation |

### Sandbox

| Code | HTTP | Meaning |
|---|---|---|
| `RT-SANDBOX-0001` | 500 | Container failed to start |
| `RT-SANDBOX-0002` | 504 | Exceeded wall-clock limit |
| `RT-SANDBOX-0003` | 500 | Result file missing or malformed |
| `RT-SANDBOX-0004` | 503 | Concurrency capacity exhausted |
| `RT-SANDBOX-0005` | 500 | Unsupported language |
| `RT-SANDBOX-0006` | — | Degraded mode: dependencies unavailable |

### Internal

| Code | HTTP | Meaning |
|---|---|---|
| `RT-INTERNAL-0001` | 500 | Unexpected internal error |
| `RT-INTERNAL-0002` | 503 | Database unavailable |
| `RT-INTERNAL-0003` | 503 | Queue unavailable |
| `RT-INTERNAL-0004` | 500 | Tenancy violation detected (should be unreachable) |
| `RT-INTERNAL-0005` | 500 | RLS policy recursion detected (should be unreachable — see B2) |
| `RT-CONFIG-0001` | — | Boot invariant violated; process refuses to start |
| `RT-CONFIG-0002` | — | Deployment tier and GitHub mode are incompatible |

Every 5xx response includes a `request_id` that correlates to a structured log entry, a trace, and — where applicable — a `pipeline_steps` row.

---

## 5. Abbreviations

| Abbr | Expansion |
|---|---|
| ADR | Architecture Decision Record |
| AST | Abstract Syntax Tree |
| ARQ | Async Redis Queue |
| GoTrue | Supabase's authentication service |
| HNSW | Hierarchical Navigable Small World (vector index) |
| PITR | Point-In-Time Recovery |
| RLS | Row-Level Security |
| RSC | React Server Component |
| SLO | Service Level Objective |
| STRIDE | Spoofing, Tampering, Repudiation, Info disclosure, DoS, Elevation of privilege |
| UUIDv7 | Time-sortable UUID |

---

*Next: [`18-CANONICAL-REGISTRY.md`](./18-CANONICAL-REGISTRY.md)*
