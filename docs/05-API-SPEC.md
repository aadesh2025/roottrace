# 05 — API Specification

> Every HTTP endpoint and WebSocket channel. Base URL `https://api.roottrace.ai`. All paths versioned under `/v1`.

---

## 1. Conventions

| Aspect | Rule |
|---|---|
| Transport | HTTPS only. HTTP redirects to HTTPS with HSTS (`max-age=63072000; includeSubDomains; preload`) |
| Content type | `application/json; charset=utf-8` |
| Casing | `snake_case` in all JSON, both directions |
| Timestamps | ISO-8601 UTC with milliseconds: `2026-08-04T09:14:22.481Z` |
| IDs | UUIDv7, prefixed on the wire: `inv_`, `iss_`, `prj_`, `evt_`, `key_`, `repo_` |
| `request_id` | `req_` + the UUIDv7 as 32 hex characters, no hyphens — one token, so it survives a copy-paste out of a support ticket and a grep out of a log line. The `req_01J2K3M4N5` forms below are abbreviated for readability, not literal. **Generated at the edge; an inbound `X-Request-ID` is ignored, never adopted** (`11` §9) |
| Money | Integer micro-USD. Never floats |
| Pagination | Cursor-based. Never offset — offset pagination breaks under concurrent inserts |
| Versioning | Path-based `/v1`. Breaking changes create `/v2`; `/v1` is supported for ≥12 months after |
| Idempotency | `Idempotency-Key` header on all POSTs. 24 h replay window |
| Compression | `gzip` and `br` accepted on request and response |
| Max body | 5 MB ingest, 1 MB elsewhere |

---

## 2. Authentication

Two distinct schemes, deliberately separated.

### 2.1 API keys — ingestion only

```http
Authorization: Bearer rt_live_a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5
```

- Format: `rt_{live|test}_{32 hex chars}`
- Scopes: `events:write` (only scope in V1)
- **Cannot** access the dashboard API. An ingest key that leaks cannot read a customer's investigations, source code, or settings — it can only write events. This separation is the entire point.
- Stored as `sha256(key)`. The plaintext is returned once at creation and is unrecoverable thereafter.

### 2.2 JWT — dashboard

```http
Authorization: Bearer eyJhbGciOiJSUzI1NiIs...
```

- Issued by Supabase GoTrue on GitHub OAuth or email magic-link sign-in.
- Access token: 1 h. Refresh token: 30 d, rotating (each use issues a new one and invalidates the old).
- Verified against Supabase's JWKS, cached 24 h with automatic refresh on `kid` miss.
- Delivered to the browser in `httpOnly; Secure; SameSite=Lax` cookies. Never in `localStorage` — an XSS then cannot exfiltrate the session.
- `sub` claim → `auth.uid()` → RLS scoping. Authorisation is enforced by the database, not by handler code.

### 2.3 Errors

```jsonc
{ "error": { "code": "RT-AUTH-0001",
             "message": "Invalid or missing API key",
             "request_id": "req_01J2K3M4N5" } }
```

| Code | HTTP | Meaning |
|---|---|---|
| `RT-AUTH-0001` | 401 | Missing or malformed credential |
| `RT-AUTH-0002` | 401 | Expired token |
| `RT-AUTH-0003` | 403 | Valid credential, insufficient scope |
| `RT-AUTH-0004` | 401 | Key revoked |
| `RT-AUTH-0005` | 403 | No access to the requested project |
| `RT-AUTH-0020` | 401 | Webhook signature verification failed |

---

## 3. Standard response envelopes

### Success — single resource

```jsonc
{ "data": { /* resource */ },
  "meta": { "request_id": "req_01J2K3M4N5", "duration_ms": 23 } }
```

### Success — collection

```jsonc
{ "data": [ /* resources */ ],
  "pagination": {
    "next_cursor": "eyJpZCI6Imludl8wMUoy...",
    "prev_cursor": null,
    "has_more": true,
    "limit": 50
  },
  "meta": { "request_id": "req_01J2K3M4N5", "duration_ms": 41, "total_estimate": 1247 } }
```

`total_estimate` is deliberately approximate — an exact `COUNT(*)` over a partitioned 100M-row table is a query nobody should run for a page header.

### Error

```jsonc
{ "error": {
    "code": "RT-VALIDATION-0001",
    "message": "Request validation failed",
    "details": [
      { "field": "events[3].error.type", "code": "required", "message": "field is required" },
      { "field": "events[7].timestamp",  "code": "out_of_range", "message": "more than 7 days in the past" }
    ],
    "request_id": "req_01J2K3M4N5",
    "documentation_url": "https://docs.roottrace.ai/errors/RT-VALIDATION-0001"
  } }
```

---

## 4. Rate limits

Every response carries:

```http
X-RateLimit-Limit: 1000
X-RateLimit-Remaining: 847
X-RateLimit-Reset: 1754301600
X-RateLimit-Scope: project
```

| Endpoint group | Free | Pro | Team | Enterprise |
|---|---|---|---|---|
| `POST /v1/events` | 60 req/min, 1k events/min | 600/min, 30k events/min | 3k/min, 150k events/min | custom |
| Dashboard reads | 120/min | 600/min | 1,200/min | custom |
| Dashboard writes | 30/min | 120/min | 300/min | custom |
| `POST .../investigate` (manual) | 5/hour | 60/hour | 300/hour | custom |
| WebSocket connections | 3 concurrent | 20 | 100 | custom |

Algorithm: sliding-window token bucket in Redis, keyed by API key (ingest) or user+project (dashboard). On exhaustion: `429` with `Retry-After` in seconds.

---

## 5. Ingestion API

### `POST /v1/events`

Auth: API key (`events:write`). Full request schema in `03` §S4.S1.

**Request**

```http
POST /v1/events HTTP/1.1
Authorization: Bearer rt_live_a3f8...
Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000
Content-Encoding: gzip

{ "events": [ /* 1–100 event objects */ ] }
```

**Response — 202**

```jsonc
{ "data": {
    "batch_id": "bat_01J2K3M4N5",
    "accepted": 98,
    "rejected": 2,
    "errors": [
      { "index": 14, "code": "RT-INGEST-0011", "message": "error.type is required" },
      { "index": 71, "code": "RT-INGEST-0012", "message": "timestamp is more than 7 days in the past" }
    ] },
  "meta": { "request_id": "req_01J2K", "duration_ms": 31 } }
```

Partial success is intentional: one malformed event must not discard 99 valid ones. The client can log the rejects without losing data.

| Error | HTTP |
|---|---|
| `RT-INGEST-0003` batch > 100 | 400 |
| `RT-INGEST-0004` body > 5 MB | 413 |
| `RT-INGEST-0010` all events invalid | 422 |
| `RT-QUOTA-0001` project quota exhausted | 402 |
| `RT-RATE-0001` rate limit | 429 |

### `POST /v1/events/test`

Validates and echoes the normalised event — including the computed fingerprint and severity — **without persisting anything.** Used by the SDK's `roottrace verify` command during integration.

---

## 6. Dashboard API

All endpoints below require JWT auth and are RLS-scoped.

### 6.1 Projects

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/projects` | List projects the user can access |
| POST | `/v1/projects` | Create |
| GET | `/v1/projects/{id}` | Detail incl. settings and quota state |
| PATCH | `/v1/projects/{id}` | Update name, description, settings |
| DELETE | `/v1/projects/{id}` | Soft delete |
| GET | `/v1/projects/{id}/health` | Health score + 30-day trend |
| GET | `/v1/projects/{id}/usage` | Usage and cost by day |

**`GET /v1/projects/{id}/health`**

```jsonc
{ "data": {
    "health_score": 0.84,
    "band": "good",
    "components": {
      "error_rate":       { "score": 0.90, "value": 12.4, "unit": "errors/hour", "trend": "down" },
      "unresolved_p0_p1": { "score": 0.75, "value": 3,    "trend": "flat" },
      "fix_rate":         { "score": 0.88, "value": 0.42, "unit": "merge_ratio", "trend": "up" },
      "mean_time_to_pr":  { "score": 0.82, "value": 312,  "unit": "seconds", "trend": "down" }
    },
    "sparkline_30d": [0.79, 0.81, 0.78, /* … */ 0.84] } }
```

### 6.2 Issues

**`GET /v1/projects/{pid}/issues`**

Query parameters:

| Param | Type | Default | Notes |
|---|---|---|---|
| `status` | enum[] | `open,investigating,regressed` | |
| `severity` | enum[] | all | |
| `environment` | enum[] | all | |
| `service` | string[] | all | |
| `search` | string | — | Trigram search over `normalized_message` and `culprit` |
| `since` / `until` | ISO-8601 | last 7 d | On `last_seen` |
| `sort` | enum | `last_seen_desc` | `last_seen_desc`, `occurrence_count_desc`, `severity_desc`, `first_seen_desc`, `rate_desc` |
| `cursor` | string | — | |
| `limit` | int | 50 | max 200 |

```jsonc
{ "data": [{
    "id": "iss_01J2K3M4N5",
    "fingerprint": "a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5",
    "error_type": "TypeError",
    "sample_message": "unsupported operand type(s) for +: 'decimal.Decimal' and 'NoneType'",
    "culprit": "services/checkout.py::calculate_total",
    "route_pattern": "/api/v2/checkout",
    "status": "investigating",
    "severity": "P1",
    "severity_score": 0.72,
    "occurrence_count": 1247,
    "first_seen": "2026-07-28T14:02:11.000Z",
    "last_seen":  "2026-08-04T09:14:22.481Z",
    "rate_per_hour": 41.3,
    "affected_user_count": 89,
    "environments": ["production"],
    "affected_releases": ["v2.14.1","v2.14.3"],
    "is_regression": false,
    "sparkline_24h": [2,4,1,0,0,3,12,41,38,44,39,41,/*…*/],
    "latest_investigation": {
      "id": "inv_01J2K3M4N5", "status": "awaiting_decision",
      "confidence": 0.836, "confidence_band": "high",
      "pr_url": "https://github.com/acme/checkout-api/pull/482" }
  }],
  "pagination": { "next_cursor": "eyJ...", "has_more": true, "limit": 50 } }
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/issues/{id}` | Full detail |
| PATCH | `/v1/issues/{id}` | Update `status`, `severity`, `muted_until` |
| GET | `/v1/issues/{id}/occurrences` | Paginated occurrence list |
| GET | `/v1/issues/{id}/timeline` | Bucketed counts for charting |
| GET | `/v1/issues/{id}/investigations` | All investigations for this issue |
| POST | `/v1/issues/{id}/investigate` | **Manually trigger an investigation** |
| POST | `/v1/issues/{id}/resolve` | Mark resolved |
| POST | `/v1/issues/{id}/mute` | `{ "until": "…", "reason": "…" }` |

**`GET /v1/issues/{id}/timeline?bucket=hour&since=…&until=…`**

```jsonc
{ "data": {
    "bucket": "hour",
    "series": [
      { "ts": "2026-08-04T08:00:00Z", "count": 38, "affected_users": 21 },
      { "ts": "2026-08-04T09:00:00Z", "count": 44, "affected_users": 27 }
    ],
    "annotations": [
      { "ts": "2026-07-25T11:04:00Z", "type": "commit",  "label": "8a3f1c2 refactor: extract tax lookup" },
      { "ts": "2026-08-01T10:00:00Z", "type": "release", "label": "v2.14.3" },
      { "ts": "2026-08-04T09:20:00Z", "type": "pr",      "label": "PR #482 opened" }
    ] } }
```

Annotations are what make the chart diagnostic rather than decorative — the visual correlation between a release marker and a rate spike is often the fastest read in the whole product.

### 6.3 Investigations

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/projects/{pid}/investigations` | List, filterable by status/band/date |
| GET | `/v1/investigations/{id}` | Full detail with all artefacts |
| GET | `/v1/investigations/{id}/steps` | Pipeline steps (drives the viewer) |
| GET | `/v1/investigations/{id}/steps/{stage}` | One stage, full input/output |
| GET | `/v1/investigations/{id}/context` | Context bundle with source content |
| GET | `/v1/investigations/{id}/reasoning` | Reasoning chain with evidence |
| GET | `/v1/investigations/{id}/patch` | Diff (`?attempt=N`, `?format=unified\|json`) |
| GET | `/v1/investigations/{id}/validation` | Validation runs, all attempts |
| GET | `/v1/investigations/{id}/validation/{attempt}/transcript` | Raw sandbox output |
| GET | `/v1/investigations/{id}/critique` | Critic review |
| GET | `/v1/investigations/{id}/confidence` | Score with full breakdown |
| GET | `/v1/investigations/{id}/llm_calls` | Every model call with tokens and cost |
| POST | `/v1/investigations/{id}/cancel` | Cancel an in-flight run |
| POST | `/v1/investigations/{id}/replay` | Re-run, optionally with different model/prompt versions |
| POST | `/v1/investigations/{id}/feedback` | Manual user verdict |
| WS | `/v1/investigations/{id}/stream` | Live pipeline updates |

**`GET /v1/investigations/{id}`** (abridged)

```jsonc
{ "data": {
    "id": "inv_01J2K3M4N5",
    "project_id": "prj_01J2K",
    "issue": { "id": "iss_01J2K", "error_type": "TypeError",
               "sample_message": "…", "occurrence_count": 1247, "severity": "P1" },
    "repository": { "id": "repo_01J2K", "full_name": "acme/checkout-api",
                    "base_commit_sha": "9f2b1c4e", "base_ref": "v2.14.3" },
    "status": "awaiting_decision",
    "current_stage": null,
    "triggered_by": "auto",
    "confidence": 0.836,
    "confidence_band": "high",
    "repair_attempts": 0,
    "totals": { "tokens_in": 64312, "tokens_out": 6108,
                "cost_micro_usd": 318000, "duration_ms": 114203 },
    "stages": [
      { "stage": "understand", "status": "completed", "duration_ms": 2814,
        "summary": "TypeError · null_undefined family · 2 in-app frames resolved" },
      { "stage": "retrieve",   "status": "completed", "duration_ms": 6120,
        "summary": "7 files · 18,412 tokens · quality 0.86" },
      { "stage": "reason",     "status": "completed", "duration_ms": 24118,
        "summary": "Root cause identified · 7-step chain · 4 evidence items" },
      { "stage": "patch",      "status": "completed", "duration_ms": 13402,
        "summary": "3 files · +36/−4 · regression test included" },
      { "stage": "validate",   "status": "completed", "duration_ms": 41226,
        "summary": "All 9 gates passed · 47/47 tests" },
      { "stage": "critique",   "status": "completed", "duration_ms": 11840,
        "summary": "approve_with_notes · 2 findings · security clean" },
      { "stage": "score",      "status": "completed", "duration_ms": 180,
        "summary": "0.836 · high" },
      { "stage": "publish",    "status": "completed", "duration_ms": 3902,
        "summary": "PR #482 opened" }
    ],
    "root_cause": { "summary": "…", "mechanism": "…", "category": "unhandled_error_path",
                    "introduced_by_sha": "8a3f1c2" },
    "pull_request": { "number": 482, "url": "https://github.com/acme/checkout-api/pull/482",
                      "state": "open", "is_draft": false, "is_simulated": false },
    "queued_at": "2026-08-04T09:14:23.100Z",
    "completed_at": "2026-08-04T09:16:17.303Z" } }
```

**`POST /v1/investigations/{id}/replay`**

```jsonc
// request
{ "from_stage": "reason",                 // replay from here; earlier artefacts reused
  "overrides": { "model_tier": { "reasoning_a": "gpt-5" },
                 "prompt_versions": { "reason": "v4" } },
  "reason": "evaluating reason.v4 against a known-good case" }
// response 202
{ "data": { "investigation_id": "inv_01J2K9NEW", "replay_of": "inv_01J2K3M4N5",
            "status": "queued" } }
```

This is how prompt and model changes are evaluated against real historical cases without touching the original record.

**Replay is bounded by source retention (C9).** Replay reconstructs from the triggering `raw_event`, which is deleted at 90 days (`04` §14). The investigation itself may be retained far longer, so availability is stated explicitly rather than inferred:

```jsonc
// on the investigation resource
{ "replay_available": true,
  "replay_available_until": "2026-11-02T09:14:23.100Z" }   // trigger event + retention
```

After that instant, `POST /replay` returns `404 RT-NOTFOUND-0002` ("source event expired") rather than failing obscurely partway through the pipeline.

### 6.4 Log explorer

**`GET /v1/projects/{pid}/logs`** — raw ingested events, the "show me everything" view.

| Param | Notes |
|---|---|
| `since` / `until` | Required. Max 30-day window per query |
| `environment`, `service`, `release` | Filters |
| `error_type` | Exact match |
| `search` | Full-text over `payload` |
| `issue_id` | Only events belonging to one issue |
| `has_investigation` | boolean |
| `is_valid` | boolean — surfaces rejected events too |
| `cursor`, `limit` | max 200 |

```jsonc
{ "data": [{
    "id": "evt_01J2K3M4N5",
    "received_at": "2026-08-04T09:14:23.012Z",
    "event_ts":    "2026-08-04T09:14:22.481Z",
    "environment": "production",
    "service": "checkout-api",
    "release": "v2.14.3",
    "error_type": "TypeError",
    "message": "unsupported operand type(s) for +: 'decimal.Decimal' and 'NoneType'",
    "route_pattern": "/api/v2/checkout",
    "status_code": 500,
    "issue_id": "iss_01J2K3M4N5",
    "investigation_id": "inv_01J2K3M4N5",
    "payload_bytes": 4821,
    "redactions": [ { "path": "request.headers.authorization", "kind": "secret_pattern" } ],
    "is_valid": true
  }] }
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/logs/{event_id}` | Full sanitised payload |
| GET | `/v1/logs/{event_id}/raw` | Original blob (audit-logged; owner/maintainer only) |
| GET | `/v1/projects/{pid}/logs/export` | Async CSV/JSON export job |
| GET | `/v1/projects/{pid}/logs/stats` | Aggregates for the header tiles |

### 6.5 Analytics

**`GET /v1/projects/{pid}/analytics/repeats`** — the "which errors keep coming back" view.

```jsonc
{ "data": {
    "period": { "since": "2026-07-05", "until": "2026-08-04" },
    "top_by_count": [
      { "issue_id": "iss_01J2K", "error_type": "TypeError",
        "culprit": "services/checkout.py::calculate_total",
        "occurrences": 1247, "unique_days": 8, "trend": "increasing",
        "growth_rate_7d": 2.4,
        "estimated_engineer_hours_saved": 3.5,
        "status": "investigating" }
    ],
    "top_by_growth": [ /* … */ ],
    "recurring_after_resolution": [
      { "issue_id": "iss_01J9Z", "resolved_at": "2026-07-12T…",
        "regressed_at": "2026-07-30T…", "occurrences_since_regression": 88 }
    ],
    "summary": {
      "total_occurrences": 8421,
      "distinct_issues": 34,
      "repeat_ratio": 0.87,
      "note": "87% of error volume came from 12% of distinct signatures"
    } } }
```

| Path | Returns |
|---|---|
| `/analytics/overview` | KPI tiles for the dashboard header |
| `/analytics/repeats` | Repeat-error analysis (above) |
| `/analytics/pipeline` | Stage success rates, durations, repair-loop frequency |
| `/analytics/confidence` | Calibration: predicted band vs. actual merge rate |
| `/analytics/cost` | Token and micro-USD breakdown by stage, model, and day |
| `/analytics/time_saved` | Estimated engineer-hours saved, with the assumptions stated |

### 6.6 Repositories and GitHub

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/projects/{pid}/repositories` | Connected repos and index status |
| POST | `/v1/projects/{pid}/repositories` | Bind an installation repo to the project |
| PATCH | `/v1/repositories/{id}` | Update `path_mappings`, `service_map`, `root_path` |
| DELETE | `/v1/repositories/{id}` | Disconnect |
| POST | `/v1/repositories/{id}/reindex` | Force full re-index (V2) |
| POST | `/v1/repositories/{id}/test_path_mapping` | **Dry-run a stack path → repo path** |
| GET | `/v1/github/install_url` | Signed App install URL |
| GET | `/v1/github/installations` | Installations visible to the user |
| POST | `/v1/github/callback` | OAuth/install redirect handler |

**`POST /v1/repositories/{id}/test_path_mapping`** — small endpoint, disproportionate value. Path resolution failures are the most common integration problem, and this makes them debuggable in ten seconds instead of by trial and error.

```jsonc
// request
{ "stack_paths": ["/app/services/checkout.py", "/usr/src/app/api/routes/checkout.py"] }
// response
{ "data": { "results": [
    { "input": "/app/services/checkout.py", "resolved": "services/checkout.py",
      "confidence": 0.95, "method": "configured_mapping", "exists_in_repo": true },
    { "input": "/usr/src/app/api/routes/checkout.py", "resolved": "api/routes/checkout.py",
      "confidence": 0.80, "method": "heuristic_prefix_strip", "exists_in_repo": true }
  ] } }
```

### 6.7 API keys

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/projects/{pid}/api_keys` | List (prefix only, never the key) |
| POST | `/v1/projects/{pid}/api_keys` | Create — **full key returned once** |
| DELETE | `/v1/api_keys/{id}` | Revoke immediately |
| POST | `/v1/api_keys/{id}/rotate` | Create a replacement, grace-expire the old |

```jsonc
// POST response — the only time `key` ever appears
{ "data": {
    "id": "key_01J2K3M4N5",
    "name": "production-checkout",
    "key": "rt_live_a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5",
    "key_prefix": "rt_live_a3f8",
    "scopes": ["events:write"],
    "environment": "production",
    "created_at": "2026-08-04T09:00:00.000Z",
    "warning": "This key will not be shown again. Store it securely." } }
```

### 6.8 Settings, members, audit

| Method | Path |
|---|---|
| GET / PATCH | `/v1/projects/{pid}/settings` |
| GET | `/v1/projects/{pid}/members` |
| POST | `/v1/projects/{pid}/members` (invite) |
| PATCH / DELETE | `/v1/projects/{pid}/members/{user_id}` |
| GET | `/v1/projects/{pid}/audit_log` |
| GET | `/v1/me` |
| PATCH | `/v1/me` |

---

## 7. WebSocket

### `WSS /v1/investigations/{id}/stream`

Auth: JWT via `Sec-WebSocket-Protocol: bearer,<token>` (browsers cannot set headers on WS upgrade).

**Server → client frames**

```jsonc
// on connect: current full state, so a late joiner sees everything
{ "type": "snapshot", "investigation": { /* full object as in GET */ } }

// per stage transition
{ "type": "stage_update", "sequence": 7, "stage": "validate", "status": "completed",
  "started_at": "…", "completed_at": "…", "duration_ms": 41226,
  "summary": "All 9 gates passed. 47/47 tests green.",
  "metrics": { "tokens_in": 0, "tokens_out": 0, "cost_micro_usd": 3000 },
  "progress": { "current_stage": 8, "total_stages": 14 },
  "investigation_status": "reviewing" }

// streaming sandbox output
{ "type": "log_line", "stage": "validate", "stream": "stdout", "sequence": 412,
  "ts": "2026-08-04T09:15:31.220Z",
  "text": "tests/test_checkout.py::test_calculate_total PASSED" }

{ "type": "status_change", "from": "reviewing", "to": "scoring" }
{ "type": "terminal", "status": "awaiting_decision", "confidence": 0.836 }
{ "type": "error", "code": "RT-PIPELINE-0007", "message": "Stage 'reason' timed out", "stage": "reason" }
{ "type": "ping", "ts": "…" }        // every 30 s
```

**Client → server**

```jsonc
{ "type": "pong" }
{ "type": "subscribe_logs",   "stage": "validate" }   // opt-in; log volume is high
{ "type": "unsubscribe_logs", "stage": "validate" }
```

Behaviour: heartbeat every 30 s, server closes after two missed pongs. Client reconnects with exponential backoff and receives a fresh `snapshot`, so no state is lost across a reconnect. Max 3 concurrent connections per user on the free plan.

### `WSS /v1/projects/{id}/stream`

Project-level firehose for the overview dashboard: `investigation_started`, `investigation_completed`, `issue_created`, `issue_spike`, `pr_opened`, `pr_merged`. Deliberately coarse — the live feed on the overview page should not need to subscribe to every stage of every run.

---

## 8. Webhooks (inbound)

### `POST /v1/webhooks/github`

HMAC-SHA256 verified via `X-Hub-Signature-256`, constant-time compare. Duplicate `X-GitHub-Delivery` IDs are ignored. Responds `202` in under 10 s, always — processing is enqueued. Details in `08` §5.

### `POST /v1/webhooks/generic` (V2)

Bearer-authenticated generic error sink for teams without an SDK, with a per-project field-mapping config.

---

## 9. Complete error code registry

| Code | HTTP | Meaning |
|---|---|---|
| `RT-AUTH-0001…0005` | 401/403 | Authentication and authorisation (§2.3) |
| `RT-AUTH-0020` | 401 | Webhook signature invalid |
| `RT-VALIDATION-0001` | 422 | Request schema validation failed |
| `RT-VALIDATION-0002` | 405 | HTTP method not allowed for this path |
| `RT-INGEST-0003` | 400 | Batch size exceeded |
| `RT-INGEST-0004` | 413 | Payload too large |
| `RT-INGEST-0010` | 422 | All events in batch invalid |
| `RT-INGEST-0011` | 422 | Required event field missing |
| `RT-INGEST-0012` | 422 | Timestamp outside accepted window |
| `RT-RATE-0001` | 429 | Rate limit exceeded |
| `RT-QUOTA-0001` | 402 | Project quota exhausted |
| `RT-QUOTA-0002` | 402 | Cost circuit breaker open |
| `RT-NOTFOUND-0001` | 404 | Resource not found or not accessible |
| `RT-NOTFOUND-0002` | 404 | Source event expired; investigation no longer replayable |
| `RT-CONFLICT-0004` | 409 | Idempotency key claimed by an in-flight duplicate |
| `RT-CONFLICT-0001` | 409 | Idempotency key reused with a different body |
| `RT-CONFLICT-0002` | 409 | Investigation already running for this issue |
| `RT-PIPELINE-0001` | 500 | Stage failed unrecoverably |
| `RT-PIPELINE-0007` | 504 | Stage timed out |
| `RT-AI-0001` | 502 | All LLM providers unavailable |
| `RT-AI-0003` | 500 | Structured output could not be parsed after repair |
| `RT-GITHUB-0001` | 502 | GitHub API error |
| `RT-GITHUB-0002` | 403 | GitHub App lacks required permission |
| `RT-GITHUB-0003` | 409 | Branch already exists |
| `RT-SANDBOX-0001` | 500 | Sandbox could not start |
| `RT-SANDBOX-0002` | 504 | Sandbox exceeded time limit |
| `RT-INTERNAL-0001` | 500 | Unexpected internal error |

Every 5xx response includes `request_id`, which correlates to a structured log entry and a trace.

---

## 10. SDK surface (Python, V1)

Distribution `roottrace-sdk`; import name `roottrace_sdk`. *(Corrected at T2.5 — this section said `import roottrace`. A distribution and import name that differ is a well-known papercut, `beautifulsoup4`/`bs4` being the canonical example, and there is no reason to inherit it. `import roottrace_sdk as roottrace` reads exactly as the examples below did.)*

```python
import roottrace_sdk as roottrace

roottrace.init(
    api_key=os.environ["ROOTTRACE_API_KEY"],   # or set ROOTTRACE_API_KEY
    environment="production",
    service="checkout-api",
    release=os.environ.get("GIT_SHA"),
    sample_rate=1.0,
    before_send=lambda e: None if e["error"]["type"] == "ClientDisconnect" else e,
    max_breadcrumbs=25,
)

# FastAPI
from roottrace_sdk.integrations.fastapi import RootTraceMiddleware
app.add_middleware(RootTraceMiddleware)

# Manual
try:
    risky()
except Exception:
    roottrace.capture_exception(tags={"cart_id": cart.id})

# Breadcrumbs — disproportionately valuable, see 03 §S1
roottrace.add_breadcrumb(category="http", message="GET tax-service/rate → 503", level="warning")

# Lifecycle — needed because sending is batched and asynchronous
roottrace.flush(timeout=2.0)   # True if the buffer emptied in time
roottrace.close(timeout=2.0)   # flush, then stop the sender
```

`before_send` receives the event as a plain `dict`, not an object — `e["error"]["type"]`, not `e.error.type`. The attribute form in earlier drafts of this section was never implementable without shipping a model class in a package whose dependency set must stay empty.

Client behaviour: batches up to 100 events or 5 s, whichever first; gzip; retries with exponential backoff on 5xx and 429; drops to a bounded local buffer (1,000 events) if the API is unreachable; **never raises into the host application** — an observability SDK that can crash the app it observes is worse than no SDK.

**Local variables are not sent by default.** `03` §S1 shows `vars` on a frame marked "// redacted", but redaction happens at ingest — by which point a password held in a plain local has already left the customer's process, and neither the entropy rule nor the pattern list catches `hunter2`. `capture_locals=True` opts in, with client-side redaction of secret-shaped variable *names*.

**`init` never raises, but a configuration mistake is written to stderr.** A malformed `api_key` disables reporting rather than degrading quietly: it would otherwise produce 401s, which the transport correctly refuses to retry, and the developer would see an application with no errors.

---

*Next: [`06-AI-ENGINE.md`](./06-AI-ENGINE.md)*
