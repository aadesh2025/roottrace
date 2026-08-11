# 12 — Observability

> We are building an observability product. Ours must be exemplary — and it doubles as the fastest debugging tool we have.

---

## 1. The three pillars, plus one

| Pillar | Tool | Purpose |
|---|---|---|
| Logs | structlog → JSON → platform aggregator | What happened, in detail |
| Metrics | Prometheus-format → Grafana | How much, how fast, how often |
| Traces | OpenTelemetry → Tempo/Honeycomb | Where the time went across services |
| **Cost** | Postgres `llm_calls` + `usage_daily` | What it cost, attributed per tenant |

Cost is a first-class pillar here, not an afterthought. In an LLM-driven system, an untracked cost regression is as damaging as an untracked latency regression, and it is invisible without deliberate instrumentation.

---

## 2. Structured logging

### 2.1 Standard fields on every log line

```jsonc
{
  "timestamp": "2026-08-04T09:15:43.326Z",
  "level": "info",
  "logger": "worker.pipeline.validate",
  "message": "validation completed",
  "service": "worker",
  "version": "1.4.2",
  "environment": "production",
  "request_id": "req_01J2K3M4N5",
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736",
  "span_id": "00f067aa0ba902b7",
  "project_id": "prj_01J2K3M4N5",
  "investigation_id": "inv_01J2K3M4N5",
  "stage": "validate",
  "duration_ms": 41226,
  "outcome": "passed"
}
```

`request_id` is generated at the edge, propagated through the queue in job metadata, and surfaced in every API error response. It is the single identifier that connects a user saying "it broke" to the exact log lines, trace, and pipeline steps.

### 2.2 Log levels — used precisely

| Level | Use | Example |
|---|---|---|
| `debug` | Development only; off in production | Prompt token counts per section |
| `info` | Normal significant events | Stage completed, PR opened, key created |
| `warning` | Degraded but handled | Provider failover, degraded sandbox mode, retrieval gap |
| `error` | Operation failed, user-visible | Stage failed, GitHub 5xx after retries |
| `critical` | System-level failure needing immediate attention | All providers down, database unreachable, RLS denial in a worker |

**No `info` logging inside hot loops.** The ingest path logs once per batch, never once per event — at 10k events/minute, per-event logging is both a cost and a latency problem.

### 2.3 Redaction

The structlog processor chain applies redaction (`11` §8.3) before any output. It is a pipeline stage, not a convention, so a developer cannot bypass it by writing `logger.info("config", **settings.dict())`.

---

## 3. Metrics catalogue

### 3.1 Ingestion

| Metric | Type | Labels | Alert |
|---|---|---|---|
| `rt_events_received_total` | counter | project, environment, service | — |
| `rt_events_rejected_total` | counter | project, reason | > 5% of received, 5 min |
| `rt_ingest_duration_seconds` | histogram | endpoint | p99 > 200 ms |
| `rt_ingest_batch_size` | histogram | — | — |
| `rt_payload_bytes` | histogram | — | — |
| `rt_redactions_total` | counter | kind | — |

### 3.2 Queue

| Metric | Type | Labels | Alert |
|---|---|---|---|
| `rt_queue_depth` | gauge | queue | `rt:pipeline` > 100 for 10 min |
| `rt_queue_oldest_job_age_seconds` | gauge | queue | > 300 s |
| `rt_job_duration_seconds` | histogram | queue, job | — |
| `rt_job_failures_total` | counter | queue, job, reason | > 10/5 min |
| `rt_job_retries_total` | counter | queue, job | — |
| `rt_dead_letter_total` | counter | queue | any increase |

### 3.3 Pipeline

| Metric | Type | Labels | Alert |
|---|---|---|---|
| `rt_investigations_started_total` | counter | project, trigger | — |
| `rt_investigations_completed_total` | counter | project, terminal_status | — |
| `rt_pipeline_stage_duration_seconds` | histogram | stage | p95 > 2× baseline |
| `rt_pipeline_stage_failures_total` | counter | stage, error_code | > 5% of stage runs |
| `rt_pipeline_end_to_end_seconds` | histogram | — | p95 > 240 s |
| `rt_repair_attempts_total` | counter | failed_gate | — |
| `rt_terminal_state_total` | counter | state | `insufficient_context` > 20% |

### 3.4 AI

| Metric | Type | Labels | Alert |
|---|---|---|---|
| `rt_llm_calls_total` | counter | stage, tier, provider, model | — |
| `rt_llm_latency_seconds` | histogram | stage, provider, model | p95 > 45 s |
| `rt_llm_tokens_total` | counter | stage, direction, model | — |
| `rt_llm_cost_micro_usd_total` | counter | project, stage, model | — |
| `rt_llm_failures_total` | counter | provider, reason | > 10% for 5 min |
| `rt_llm_failover_total` | counter | from_provider, to_provider | any sustained increase |
| `rt_schema_repair_total` | counter | stage | > 15% of calls |
| `rt_evidence_validation_failures_total` | counter | stage | > 10% of findings |
| `rt_suspicious_content_total` | counter | project | > 10/hour |

### 3.5 Sandbox

| Metric | Type | Labels | Alert |
|---|---|---|---|
| `rt_sandbox_runs_total` | counter | language, mode, result | — |
| `rt_sandbox_duration_seconds` | histogram | language, gate | **p95 > 45 s** (the target; the 90 s kill is a separate failure signal) |
| `rt_sandbox_gate_result_total` | counter | gate, result | G4 invalid > 25% |
| `rt_sandbox_timeouts_total` | counter | language | > 5/10 min |
| `rt_sandbox_peak_memory_mb` | histogram | language | — |
| `rt_sandbox_concurrent` | gauge | — | at cap for > 5 min |
| `rt_sandbox_orphans_reaped_total` | counter | — | any increase |
| `rt_sandbox_degraded_total` | counter | reason | > 20% of runs |

### 3.6 GitHub

| Metric | Type | Labels | Alert |
|---|---|---|---|
| `rt_github_requests_total` | counter | endpoint, status | — |
| `rt_github_rate_limit_remaining` | gauge | installation | < 500 |
| `rt_github_cache_hits_total` | counter | kind (tree/blob/etag) | hit ratio < 60% |
| `rt_github_webhook_received_total` | counter | event, action | — |
| `rt_github_webhook_signature_failures_total` | counter | — | any occurrence |
| `rt_prs_opened_total` | counter | project, band | — |
| `rt_prs_merged_total` | counter | project, outcome | — |

### 3.7 Quality — the metrics that matter most

| Metric | Type | Target |
|---|---|---|
| `rt_confidence_distribution` | histogram | — |
| `rt_merge_rate` | gauge (band) | ≥ 0.40 overall, ≥ 0.75 for high band |
| `rt_calibration_error` | gauge (band) | ≤ 0.10 |
| `rt_first_attempt_validation_pass_rate` | gauge | ≥ 0.60 |
| `rt_post_repair_pass_rate` | gauge | ≥ 0.85 |
| `rt_root_cause_accuracy` | gauge (eval only) | ≥ 0.80 |
| `rt_time_to_pr_seconds` | histogram | p50 ≤ 360 s |
| `rt_false_confidence_rate` | gauge | ≤ 0.05 |

`rt_false_confidence_rate` — scored ≥ 0.80 but rejected by a human — is the single most important quality metric. It measures whether our confidence number can be believed, and everything the product claims rests on that.

---

## 4. Distributed tracing

### 4.1 Span hierarchy

```
POST /v1/events                                          [api]      31ms
├─ auth.resolve_api_key                                              4ms
├─ ratelimit.check                                                   1ms
├─ validate.schema                                                   8ms
├─ sanitize.redact                                                   6ms
├─ db.insert_raw_events                                              9ms
└─ queue.enqueue                                                     3ms

pipeline.run  inv_01J2K                                  [worker] 114.2s
├─ stage.understand                                                2.81s
│  └─ llm.complete  tier=fast model=…                              2.64s
├─ stage.retrieve                                                  6.12s
│  ├─ github.fetch_tree                             (cache hit)    0.02s
│  ├─ github.fetch_file × 4                              parallel  1.84s
│  ├─ treesitter.parse × 4                                         0.41s
│  ├─ pgvector.similarity_search                                   0.28s
│  ├─ github.blame                                                 0.94s
│  └─ rank_and_trim                                                0.11s
├─ stage.reason                                                   24.12s
│  ├─ prompt.assemble                                              0.08s
│  ├─ llm.complete  tier=reasoning_a                              23.61s
│  └─ validate.evidence_binding                                    0.43s
├─ stage.patch                                                    13.40s
├─ stage.validate                                                 41.23s
│  ├─ gate.G0_diff_applies                                         0.004s
│  ├─ gate.G1_syntax                                               0.047s
│  ├─ sandbox.create                                               1.21s
│  ├─ sandbox.exec                                                39.82s
│  │  ├─ gate.G2_dependencies                                      4.82s
│  │  ├─ gate.G3_compile                                           2.94s
│  │  ├─ gate.G4_regression_pre                                    5.11s
│  │  ├─ gate.G5_regression_post                                   4.80s
│  │  ├─ gate.G6_existing_tests                                   14.20s
│  │  ├─ gate.G7_static_analysis                                   7.40s
│  │  └─ gate.G8_security_scan                                     1.90s
│  └─ sandbox.destroy                                              0.16s
├─ stage.critique                                                 11.84s
├─ stage.score                                                     0.18s
└─ stage.publish                                                   3.90s
```

### 4.2 Span attributes

Every span carries `project_id`, `investigation_id`, and `stage`. LLM spans additionally carry `tier`, `provider`, `model`, `prompt_version`, `tokens_in`, `tokens_out`, `cost_micro_usd`, `attempt`. Sandbox spans carry `language`, `mode`, `container_image`, `peak_memory_mb`.

### 4.3 Sampling

| Category | Rate |
|---|---|
| Ingest requests | 1% (high volume, low variance) |
| Dashboard requests | 5% |
| Pipeline runs | **100%** — low volume, extremely high diagnostic value |
| Any errored operation | **100%**, always |
| Slow operations (> p95) | 100% via tail sampling |

Tracing every pipeline run is affordable because there are hundreds per day, not millions, and each trace is worth far more than its storage cost when something goes wrong.

---

## 5. Cost observability

### 5.1 Real-time attribution

Every LLM call writes an `llm_calls` row synchronously with the call. No sampling, no aggregation delay. This is a financial record, and it needs to be exact.

### 5.2 Dashboards

**Cost per investigation over time** — the headline metric. A prompt change that raises quality 2% while raising cost 60% must be immediately visible.

**Cost by stage** — expected roughly: S6 ~44%, S7 ~28%, S10 ~22%, everything else ~6%. A shift here indicates a retrieval regression (larger prompts) or a repair-loop regression (more attempts).

**Cost by project** — quota tracking, plan-fit analysis, and abuse detection.

**Token efficiency** — mean prompt tokens per investigation. Rising prompt tokens with flat quality means retrieval is getting less precise, which is a real regression even though nothing is failing.

### 5.3 Circuit breaker

**Atomic reservation, not check-then-act (B9).** Reading the spend and then proceeding lets every concurrent worker pass the check before any of them writes a cost row, so the overshoot scales with `rt:pipeline` concurrency. The breaker reserves first:

```python
async def reserve_budget(project_id: UUID, estimate_micro_usd: int) -> Reservation:
    project = await get_project(project_id)
    key = f"cost:{project_id}:{utcdate()}"

    reserved = await redis.incrby(key, estimate_micro_usd)      # atomic
    await redis.expire(key, 172_800)                            # 48h, > 1 day boundary

    if reserved > project.daily_cost_cap_micro_usd:
        await redis.decrby(key, estimate_micro_usd)             # release immediately
        await open_breaker(project_id, reason="daily_cap")
        await notify_owners(project_id, template="cost_cap_reached")
        raise QuotaExhausted("RT-QUOTA-0002")

    if reserved >= project.daily_cost_cap_micro_usd * 0.8:
        await notify_owners_once(project_id, template="cost_cap_warning_80")

    return Reservation(key=key, amount=estimate_micro_usd)


async def reconcile(reservation: Reservation, actual_micro_usd: int) -> None:
    """Runs on EVERY terminal path — success, failure, and cancellation."""
    await redis.decrby(reservation.key, reservation.amount - actual_micro_usd)
```

Worst-case overshoot is **one estimate** ($0.42 by default), independent of concurrency. Reconciliation on every terminal path is what stops abandoned reservations from silently consuming the cap — a run that fails at S6 must return its unspent reservation, or the project's effective cap ratchets down over the day.

When the breaker opens, new investigations queue as `blocked_quota` rather than failing. The UI states plainly what happened and what to do about it. Errors continue to be ingested and grouped — observability never stops, only the expensive analysis pauses.

---

## 6. Alerting

### 6.1 Philosophy

Alerts are for things a human must act on **now**. Everything else is a dashboard. An alert that fires and is routinely ignored has trained the team to ignore alerts, which is worse than having no alert at all.

### 6.2 Page immediately (SEV1/SEV2)

| Alert | Condition |
|---|---|
| API down | Health check failing 2 min |
| Database unreachable | Connection failures > 10/min |
| All LLM providers failing | `rt_llm_failures_total` 100% for 3 min |
| Ingest failure rate | > 10% for 5 min |
| Cross-tenant access attempt | Any occurrence |
| Webhook signature failure | Any occurrence |
| Sandbox egress attempt | Any occurrence |
| Dead-letter queue growth | Any increase |
| `rt_auth` helper executed by an unexpected role | Any occurrence |
| Cost reservation leaked (released without reconciliation) | Any occurrence |
| Investigation insert rejected by `investigations_one_active_per_issue` at an abnormal rate | > 20/min (indicates a triage-gate defect, not normal contention) |

### 6.3 Notify during business hours (SEV3)

| Alert | Condition |
|---|---|
| Queue depth sustained | `rt:pipeline` > 100 for 15 min |
| Pipeline p95 latency | > 240 s for 30 min |
| First-attempt validation pass rate | < 0.45 for 2 h |
| GitHub rate limit low | < 500 remaining on any installation |
| Cost anomaly | Daily spend > 2× 7-day average |
| Schema repair rate | > 15% for 1 h |
| Degraded sandbox mode | > 20% of runs for 1 h |

### 6.4 Dashboard only

Confidence distribution shifts, merge-rate trends, retrieval quality distribution, per-model latency comparison, cache hit ratios.

---

## 7. Dashboards

| Dashboard | Audience | Panels |
|---|---|---|
| **System health** | On-call | Request rate, error rate, p50/p95/p99 latency, queue depths, worker count, DB connections |
| **Pipeline health** | Engineering | Stage success rates, stage duration heatmap, terminal-state distribution, repair-loop frequency by gate |
| **AI quality** | AI/ML | Confidence distribution, calibration curve, merge rate by band, root-cause accuracy (eval), evidence-validation failure rate |
| **Cost** | Engineering + finance | Cost/investigation, cost by stage, cost by project, token efficiency, quota headroom |
| **Sandbox** | Engineering | Run rate, gate pass rates, duration by gate, timeout rate, degraded-mode rate, concurrency |
| **GitHub** | Engineering | Request rate, rate-limit headroom, cache hit ratio, PR open/merge rates, webhook lag |
| **Business** | Product | Active projects, investigations/week, PRs merged, estimated hours saved, retention cohort |

---

## 8. SLOs

| SLO | Target | Window | Error budget |
|---|---|---|---|
| Ingest availability | 99.9% | 30 d | 43 min |
| Ingest latency p99 | < 200 ms | 30 d | 1% of requests |
| Dashboard availability | 99.5% | 30 d | 3.6 h |
| Pipeline completion rate | 95% reach terminal | 30 d | 5% |
| Pipeline p95 latency | < 240 s | 30 d | 5% |
| Data durability | 100% (no accepted event lost) | ∞ | 0 |

Ingest is held to a higher standard than the dashboard, and durability to an absolute one. A customer can tolerate a slow dashboard for ten minutes; losing their production errors during an incident is an unrecoverable failure of the product's basic promise.

---

## 9. Health checks

```
GET /health          → 200 {"status":"ok","version":"1.4.2"}     (liveness, no deps)
GET /health/ready    → 200/503 with per-dependency status         (readiness)
GET /health/deep     → full diagnostic, authenticated, ops only
```

```jsonc
// /health/ready
{ "status": "ok",
  "checks": {
    "database":  { "status": "ok", "latency_ms": 3 },
    "redis":     { "status": "ok", "latency_ms": 1 },
    "storage":   { "status": "ok", "latency_ms": 12 },
    "llm_gateway": { "status": "degraded",
                     "detail": "provider_a unavailable, failing over to provider_b" }
  } }
```

Readiness returns 503 only when the service genuinely cannot serve. A degraded LLM gateway does not fail readiness — ingestion still works perfectly, and taking the API out of rotation would turn a partial degradation into a total outage.

---

## 10. Debugging playbook

### "An investigation is stuck"

```
1. GET /v1/investigations/{id} → current_stage, status
2. Query pipeline_steps for that investigation → which stage, which attempt, error_code
3. Trace by investigation_id → where the time went
4. Logs filtered by investigation_id → the error detail
5. If the stage is `validate`: check sandbox metrics for timeouts or concurrency saturation
6. If the stage is `reason` or `patch`: check llm_calls for failover, schema repair, token counts
7. Resolution: POST /v1/investigations/{id}/replay?from_stage=<stage>
```

### "Costs spiked"

```
1. Cost dashboard → which project, which stage, which day
2. Query llm_calls grouped by stage and model for the window
3. Check mean prompt tokens — rising prompt tokens means a retrieval regression
4. Check repair-attempt rate — rising repairs means a patch-quality regression
5. Check for a prompt_version change correlating with the inflection
6. Immediate mitigation: lower the project cost cap; investigate at leisure
```

### "Quality dropped"

```
1. AI quality dashboard → merge rate and first-attempt pass rate by day
2. Correlate with prompt_version and model deploys (both are recorded per call)
3. Run the eval harness against the fixture corpus on both versions
4. If a prompt version regressed: revert via config (no deploy required)
5. If a model changed upstream: pin to the previous version in models.yaml
```

The fact that every run records its `model` and `prompt_version` is what makes this playbook a ten-minute investigation rather than a week of guesswork.

---

*Next: [`13-DEPLOYMENT.md`](./13-DEPLOYMENT.md)*
