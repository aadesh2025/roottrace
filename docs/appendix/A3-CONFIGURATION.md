# A3 — Configuration Reference

> Every environment variable, feature flag, and per-project setting.

---

## 1. Environment variables

All variables are prefixed `RT_`. Typed and validated by a Pydantic `Settings` class at boot. **The process refuses to start on missing or invalid values** — there are no silent defaults for anything security-relevant.

### Core

| Variable | Type | Default | Required | Notes |
|---|---|---|---|---|
| `RT_ENVIRONMENT` | enum | — | ✅ | `local` \| `ci` \| `staging` \| `production`. **Where** it runs |
| `RT_DEPLOYMENT_TIER` | enum | `evaluation` | ✅ | `evaluation` \| `live`. **What** it is allowed to touch. See §1.1 |
| `RT_VERSION` | string | — | ✅ | Git SHA or semver; appears in every log line |
| `RT_LOG_LEVEL` | enum | `info` | | `debug` forbidden when `RT_ENVIRONMENT=production` |
| `RT_SERVICE_NAME` | string | — | ✅ | `api` \| `worker` |

### 1.1 Environment vs deployment tier (C5)

These are **two orthogonal axes**, and conflating them is what produced C5: the original invariant asserted `github_mode == "live"` whenever `environment == "production"`, which made a V1 production deployment unbootable, since V1 is fixture-only by mandate (`A4-ADR-007`).

| Axis | Question it answers | Values |
|---|---|---|
| `RT_ENVIRONMENT` | Where does this run? What are the operational expectations — logging, tracing, gVisor, rate limits? | `local`, `ci`, `staging`, `production` |
| `RT_DEPLOYMENT_TIER` | What is this deployment permitted to touch? | `evaluation`, `live` |

| Tier | Meaning | `GITHUB_MODE` | Customer repos |
|---|---|---|---|
| `evaluation` | **V1.** Runs the complete pipeline against fixtures. Provably cannot reach a customer repository | `fixture` or `replay` — **`live` is refused at boot** | None. No installation tokens are ever minted |
| `live` | **V2+.** Real repositories | `live` — **`fixture` is refused at boot** | Real, per installation |

A V1 deployment is therefore legitimately `RT_ENVIRONMENT=production` + `RT_DEPLOYMENT_TIER=evaluation`: full production operational rigour (gVisor, tracing, rate limits, no debug logging) with a hard structural guarantee that it cannot write to anyone's repository. That combination is what V1 actually is, and the old invariant could not express it.

**The tier is a safety interlock, not a convenience.** `evaluation` refusing `GITHUB_MODE=live` means a misconfigured V1 deployment fails at boot rather than discovering at S12 that it holds a real installation token.

### Database

| Variable | Type | Default | Required | Notes |
|---|---|---|---|---|
| `RT_DATABASE_URL` | PostgresDsn | — | ✅ | Connection string |
| `RT_DATABASE_POOL_SIZE` | int | 20 | | Per replica |
| `RT_DATABASE_MAX_OVERFLOW` | int | 10 | | |
| `RT_DATABASE_POOL_TIMEOUT` | int | 30 | | Seconds |
| `RT_DATABASE_STATEMENT_TIMEOUT_MS` | int | 30000 | | Server-side kill |

### Redis

| Variable | Type | Default | Required |
|---|---|---|---|
| `RT_REDIS_URL` | RedisDsn | — | ✅ |
| `RT_REDIS_MAX_CONNECTIONS` | int | 50 | |
| `RT_QUEUE_PREFIX` | string | `rt:` | |

### Supabase

| Variable | Type | Required | Notes |
|---|---|---|---|
| `RT_SUPABASE_URL` | HttpUrl | ✅ | |
| `RT_SUPABASE_ANON_KEY` | SecretStr | ✅ | Public by design; safe in the browser |
| `RT_SUPABASE_SERVICE_ROLE_KEY` | SecretStr | ✅ (worker) | **Bypasses RLS.** Worker only. Never in `api`, never near a sandbox |
| `RT_SUPABASE_JWKS_URL` | HttpUrl | ✅ (api) | RS256 public-key set. See B12 below |
| `RT_SUPABASE_JWKS_CACHE_TTL_SECONDS` | int | 86400 | Refetched early on a `kid` miss |
| `RT_STORAGE_BUCKET` | string | | Default `roottrace-artifacts` |

> **B12 — `RT_SUPABASE_JWT_SECRET` is retired.** The specification previously contradicted itself: `05` §2.2 and `11` §3.1 described RS256 verified against cached JWKS, while this appendix required a symmetric HS256 shared secret. RS256 + JWKS is canonical. The shared secret is not merely redundant — it is a **signing** key, so its presence in the `api` environment would let a compromised API process mint valid tokens for any user. A public key cannot. Retiring it removes a forgery capability from the service that is most exposed to the internet.

### GitHub

| Variable | Type | Default | Required | Notes |
|---|---|---|---|---|
| `RT_GITHUB_MODE` | enum | `fixture` | | `fixture` \| `replay` \| `live`. Constrained by `RT_DEPLOYMENT_TIER` (§1.1) |
| `RT_GITHUB_APP_ID` | int | — | tier=live | |
| `RT_GITHUB_PRIVATE_KEY` | SecretStr | — | tier=live | PEM. From secret manager, never a file in the image |
| `RT_GITHUB_WEBHOOK_SECRET` | SecretStr | — | tier=live | HMAC key |
| `RT_GITHUB_CLIENT_ID` | string | — | see §5.1 | OAuth login. Optional locally |
| `RT_GITHUB_CLIENT_SECRET` | SecretStr | — | see §5.1 | OAuth login. Optional locally |
| `RT_GITHUB_API_BASE` | HttpUrl | `https://api.github.com` | | GHE override |
| `RT_GITHUB_MAX_CONCURRENCY` | int | 8 | | Per installation |
| `RT_GITHUB_FIXTURE_PATH` | Path | `fixtures/synthetic-repo` | | Fixture mode |

### LLM

| Variable | Type | Default | Notes |
|---|---|---|---|
| `RT_LLM_CONFIG_PATH` | Path | `infra/config/models.yaml` | Tier routing |
| `RT_ANTHROPIC_API_KEY` | SecretStr | — | If used |
| `RT_OPENAI_API_KEY` | SecretStr | — | If used |
| `RT_VOYAGE_API_KEY` | SecretStr | — | Embeddings |
| `RT_LLM_TIMEOUT_SECONDS` | int | 60 | Per call |
| `RT_LLM_MAX_RETRIES` | int | 3 | Per provider |
| `RT_LLM_CACHE_TTL_SECONDS` | int | 3600 | Deterministic-stage cache |
| `RT_LLM_ENABLE_PROMPT_CACHING` | bool | true | Provider-side caching of static layers |

### Sandbox

| Variable | Type | Default | Notes |
|---|---|---|---|
| `RT_SANDBOX_ENABLED` | bool | true | Kill switch |
| `RT_SANDBOX_RUNTIME` | enum | `runsc` | `runc` \| `runsc` (gVisor). **`runsc` required in production** |
| `RT_SANDBOX_CONCURRENCY` | int | 4 | Global semaphore |
| `RT_SANDBOX_TIMEOUT_SECONDS` | int | **90** | Hard SIGKILL. p95 **target** is 45 s — see B11 in `03` §S8 |
| `RT_SANDBOX_TARGET_P95_SECONDS` | int | 45 | Alerting threshold only; never kills |
| `RT_SANDBOX_MEMORY_LIMIT_MB` | int | 512 | |
| `RT_SANDBOX_CPU_LIMIT` | float | 1.0 | |
| `RT_SANDBOX_PIDS_LIMIT` | int | 128 | Fork-bomb containment |
| `RT_SANDBOX_DISK_LIMIT_MB` | int | 256 | |
| `RT_SANDBOX_MAX_STDOUT_BYTES` | int | 524288 | 512 KB |
| `RT_SANDBOX_IMAGE_PYTHON` | string | `roottrace/sandbox-python:3.12` | Digest-pinned in production |
| `RT_SANDBOX_IMAGE_NODE` | string | `roottrace/sandbox-node:20` | |
| `RT_SANDBOX_REAPER_INTERVAL_SECONDS` | int | 60 | |
| `RT_SANDBOX_ORPHAN_MAX_AGE_SECONDS` | int | 120 | |

### Pipeline

| Variable | Type | Default | Notes |
|---|---|---|---|
| `RT_PIPELINE_MAX_REPAIR_ATTEMPTS` | int | 3 | |
| `RT_PIPELINE_CONTEXT_TOKEN_BUDGET` | int | 24000 | **The single largest cost lever** |
| `RT_PIPELINE_STAGE_TIMEOUT_SECONDS` | json | see `03` §6 | **Hard kill limits only.** Per-stage override map, e.g. `{"reason": 60, "validate": 90}`. These are *not* p95 targets — `03` §6 is canonical for both, and the two columns must never be conflated |
| `RT_PIPELINE_DEFAULT_COOLDOWN_HOURS` | int | 6 | |
| `RT_PIPELINE_MIN_CONTEXT_FILES` | int | 3 | Below this → `insufficient_context` |
| `RT_PIPELINE_MIN_CONTEXT_TOKENS` | int | 800 | In-app source only |

### Rate limits and quotas

| Variable | Type | Default |
|---|---|---|
| `RT_RATELIMIT_ENABLED` | bool | true |
| `RT_RATELIMIT_INGEST_PER_MINUTE` | json | per-plan map |
| `RT_RATELIMIT_DASHBOARD_PER_MINUTE` | json | per-plan map |
| `RT_DEFAULT_DAILY_COST_CAP_MICRO_USD` | int | 5000000 ($5) |
| `RT_DEFAULT_MONTHLY_COST_CAP_MICRO_USD` | int | 100000000 ($100) |
| `RT_COST_WARNING_THRESHOLD` | float | 0.8 |
| `RT_COST_RESERVATION_ENABLED` | bool | true | Atomic pre-reservation (B9). Disabling permits cap overshoot proportional to `rt:pipeline` concurrency |
| `RT_COST_RESERVATION_ESTIMATE_MICRO_USD` | int | 420000 | Reserved per investigation before S4 ($0.42 = one-repair path). Reconciled to actual on completion |

### Observability

| Variable | Type | Default |
|---|---|---|
| `RT_OTEL_ENDPOINT` | HttpUrl | — (required in production) |
| `RT_OTEL_SAMPLE_RATE_INGEST` | float | 0.01 |
| `RT_OTEL_SAMPLE_RATE_DASHBOARD` | float | 0.05 |
| `RT_OTEL_SAMPLE_RATE_PIPELINE` | float | 1.0 |
| `RT_METRICS_PORT` | int | 9090 |
| `RT_SENTRY_DSN` | SecretStr | — (our own error tracking) |

---

## 2. Feature flags

Read at **request time**, not at boot — a bad prompt version or misbehaving sandbox can be disabled in seconds without a deploy.

| Flag | Default | Effect when enabled |
|---|---|---|
| `RT_FF_SANDBOX_ENABLED` | true | Sandbox validation runs. When false, pipeline degrades to syntax-only and says so |
| `RT_FF_AUTO_MERGE` | **false** | Global auto-merge kill switch. Off until V3 |
| `RT_FF_MULTI_MODEL_CONSENSUS` | false | V2: N models on stage 6 for P0/P1 |
| `RT_FF_CI_SECOND_GATE` | false | V2: poll GitHub Checks after publish |
| `RT_FF_REPO_INDEXING` | false | V2: AST indexing and embeddings |
| `RT_FF_VECTOR_RETRIEVAL` | false | V2: retrieval strategy C |
| `RT_FF_INVESTIGATION_CHAT` | false | V4: chat over an investigation |
| `RT_FF_PROMPT_VERSION_OVERRIDE` | null | JSON map, e.g. `{"reason":"v4"}` — A/B evaluation |
| `RT_FF_MODEL_TIER_OVERRIDE` | null | JSON map overriding `models.yaml` |
| `RT_FF_STRICT_EVIDENCE_BINDING` | true | Discard unbound findings. **Never disable in production** |

---

## 3. Per-project settings (`projects.settings` JSONB)

```jsonc
{
  // ── Investigation gating ────────────────────────────────────────────
  "min_investigation_severity": "P2",           // P0|P1|P2|P3
  "investigated_environments": ["production"],
  "investigation_cooldown_hours": 6,
  "max_concurrent_investigations": 5,

  // ── Publication ─────────────────────────────────────────────────────
  "confidence_floor_for_pr": 0.40,
  "draft_pr_below_confidence": 0.60,
  "pr_labels": ["roottrace"],
  "pr_reviewers": [],                            // GitHub usernames to request

  // ── Auto-merge (V3; off in V1/V2) ──────────────────────────────────
  "auto_merge_enabled": false,
  "auto_merge_paths": [],                        // globs, e.g. ["tests/**"]
  "auto_merge_min_confidence": 0.90,
  "auto_merge_daily_limit": 3,
  "auto_merge_denylist_paths": [
    ".github/**", "Dockerfile", "**/migrations/**",
    "**/auth/**", "**/security/**", "requirements.txt", "package.json"
  ],

  // ── Severity scoring ────────────────────────────────────────────────
  "endpoint_criticality": {
    "/api/v2/checkout": 1.0,
    "/api/v2/auth/*":   0.9,
    "/api/v2/search":   0.4,
    "/health":          0.0
  },
  "severity_weights": {
    "rate": 0.30, "users": 0.25, "criticality": 0.20,
    "environment": 0.15, "novelty": 0.10
  },

  // ── Fingerprinting ──────────────────────────────────────────────────
  "fingerprint_rules": [
    { "match": { "error.type": "HTTPError" },
      "group_by": ["error.type", "request.route_pattern", "request.status_code"] }
  ],

  // ── Path resolution ─────────────────────────────────────────────────
  "path_mappings": [
    { "from": "/app/", "to": "" },
    { "from": "/usr/src/app/", "to": "" }
  ],

  // ── Model preferences ───────────────────────────────────────────────
  "model_tier_overrides": {},                    // {"reasoning_a": "gpt-5"}
  "use_customer_llm_keys": false,

  // ── Notifications (V2) ──────────────────────────────────────────────
  "notify_on": ["pr_opened", "investigation_failed", "cost_cap_warning"],
  "slack_webhook_url": null,
  "notification_emails": [],

  // ── Noise control (V2) ──────────────────────────────────────────────
  "muted_error_types": [],
  "muted_paths": [],
  "min_occurrences_before_investigation": 1,

  // ── Value reporting ─────────────────────────────────────────────────
  "engineer_hours_per_fix_assumption": 2.1       // used for "time saved"; shown in tooltip
}
```

---

## 4. Model routing (`infra/config/models.yaml`)

```yaml
tiers:
  fast:
    - { provider: anthropic, model: claude-haiku-4-5, max_tokens: 4096, timeout_s: 15 }
    - { provider: openai,    model: gpt-4.1-mini,     max_tokens: 4096, timeout_s: 15 }
  reasoning-a:
    - { provider: anthropic, model: claude-sonnet-5,  max_tokens: 8192, timeout_s: 60 }
    - { provider: openai,    model: gpt-5,            max_tokens: 8192, timeout_s: 60 }
  reasoning-b:
    - { provider: openai,    model: gpt-5,            max_tokens: 8192, timeout_s: 60 }
    - { provider: anthropic, model: claude-sonnet-5,  max_tokens: 8192, timeout_s: 60 }
  embed:
    - { provider: voyage,    model: voyage-code-3,    dimensions: 1536 }
    - { provider: openai,    model: text-embedding-3-large, dimensions: 1536 }

stage_tiers:
  understand:     fast
  reason:         reasoning-a
  patch:          reasoning-a
  critique:       reasoning-b       # different provider — independence is the point
  repair:         fast
  pr_description: fast
  feedback:       fast

failover:
  trigger_on: [rate_limit, timeout, server_error, content_filter]
  max_provider_attempts: 2
  backoff: { base_ms: 1000, factor: 2, jitter: true, max_ms: 16000 }

pricing_micro_usd_per_1k:      # for cost accounting; update when providers change
  claude-sonnet-5:  { input: 3000,  output: 15000 }
  claude-haiku-4-5: { input: 300,   output: 1500 }
  gpt-5:            { input: 3000,  output: 15000 }
  gpt-4.1-mini:     { input: 300,   output: 1500 }
  voyage-code-3:    { input: 20,    output: 0 }
```

---

## 5. Local development

```bash
# .env.local
RT_ENVIRONMENT=local
RT_DEPLOYMENT_TIER=evaluation
RT_VERSION=dev
RT_LOG_LEVEL=debug
RT_SERVICE_NAME=api

RT_DATABASE_URL=postgresql://postgres:postgres@localhost:54322/postgres
RT_REDIS_URL=redis://localhost:6379/0

RT_SUPABASE_URL=http://localhost:54321
RT_SUPABASE_ANON_KEY=<from `supabase status`>
RT_SUPABASE_SERVICE_ROLE_KEY=<from `supabase status`>   # worker only
RT_SUPABASE_JWKS_URL=http://localhost:54321/auth/v1/.well-known/jwks.json

# GitHub OAuth login — OPTIONAL locally. See §5.1.
# RT_GITHUB_CLIENT_ID=
# RT_GITHUB_CLIENT_SECRET=

RT_GITHUB_MODE=fixture
RT_GITHUB_FIXTURE_PATH=fixtures/synthetic-repo

RT_ANTHROPIC_API_KEY=sk-ant-...

RT_SANDBOX_ENABLED=true
RT_SANDBOX_RUNTIME=runc          # gVisor often unavailable locally
RT_SANDBOX_CONCURRENCY=2
RT_SANDBOX_TIMEOUT_SECONDS=90

RT_FF_STRICT_EVIDENCE_BINDING=true
```

```bash
make dev        # supabase start · redis · api · worker
make fixtures-reset
make fixture-run CASE=null-prop-01
```

### 5.1 Local authentication (A4)

GitHub OAuth requires a registered OAuth app and a callback URL, which not every developer will have on day one. **Missing OAuth credentials must never block backend or database work.**

| Context | Primary | Fallback | Enforced by |
|---|---|---|---|
| `local` | GitHub OAuth **when `RT_GITHUB_CLIENT_ID`/`SECRET` are set** | Supabase email magic link — full JWT, identical claims, identical RLS behaviour | Auto-detected at boot; the login page shows whichever providers are configured |
| `ci` | Magic link only (deterministic, no external dependency) | — | Test fixtures mint sessions directly through GoTrue |
| `staging` / `production` | GitHub OAuth **required** | Magic link stays enabled for account recovery | Boot invariant (§6) |

The critical property: **both paths issue the same RS256 JWT with the same `sub` claim**, so `auth.uid()` and every RLS policy behave identically. A developer working with magic link is exercising the real authorization path, not a bypass. There is no "dev mode" that skips auth — that would leave the auth path untested until staging.

### 5.2 Make targets (A1) — CANONICAL

The Makefile is the canonical local developer interface. **CI invokes these same targets**, so local and CI cannot diverge — a build that passes `make check` locally passes CI's check job by construction.

| Target | Does | Used by |
|---|---|---|
| `make check` | `fmt-check` → `lint` → `typecheck` → `test-unit`. The pre-push gate | Developer + CI `check` job |
| `make fmt` | `ruff format`, `prettier --write` | Developer |
| `make fmt-check` | Same, `--check` — fails on unformatted code | `check` |
| `make lint` | `ruff check`, `eslint` | `check` |
| `make typecheck` | `mypy --strict`, `tsc --noEmit` | `check` |
| `make test-unit` | `pytest -m unit`, `vitest run` | `check` |
| `make test-integration` | `pytest -m integration` (testcontainers: Postgres + Redis) | CI `integration` job |
| `make test-security` | `pytest -m security` — RLS, sandbox isolation, injection corpus | CI `security` job |
| `make test-e2e` | `playwright test` | CI `e2e` job |
| `make dev` | `supabase start` · redis · api · worker (web from the frontend phase) | Developer |
| `make db-reset` | `supabase db reset` + seed | Developer |
| `make fixtures-reset` | Rebuild the fixture DB and load the synthetic repo | Developer + CI |
| `make fixture-run CASE=<id>` | One fixture case end to end | Developer |
| `make fixtures-verify` | Assert every ground-truth path/symbol/line resolves to real code | CI |
| `make eval` | Full 25-case corpus × 3 runs, all metrics | CI (prompt-touching PRs) + nightly |
| `make eval-compare BASELINE=<v> CANDIDATE=<v>` | Paired baseline-vs-candidate comparison with cost delta | Prompt promotion |
| `make ci` | Everything CI runs, in CI order. Reproduces a CI failure locally | Developer |

---

## 6. Boot invariants

Enforced by a Pydantic `model_validator`. **The process will not boot if any fail.** A misconfigured service that starts and behaves subtly wrongly is far worse than one that fails loudly.

The invariants split along the two axes from §1.1: operational rigour keys on `RT_ENVIRONMENT`, blast radius keys on `RT_DEPLOYMENT_TIER`.

```python
@model_validator(mode="after")
def boot_invariants(self):

    # ── Tier invariants: what this deployment may touch (C5) ──────────────
    if self.deployment_tier == "evaluation":
        assert self.github_mode in ("fixture", "replay"), \
            "SECURITY: evaluation tier must not use live GitHub"
        assert self.github_private_key is None, \
            "SECURITY: evaluation tier must not hold a GitHub App key"
    else:                                        # tier == "live"
        assert self.github_mode == "live",       "live tier requires live GitHub"
        assert self.github_app_id is not None
        assert self.github_private_key is not None
        assert self.github_webhook_secret is not None

    # ── Environment invariants: operational rigour ────────────────────────
    if self.environment == "production":
        assert self.log_level != "debug",        "debug logging forbidden in production"
        assert self.sandbox_runtime == "runsc",  "gVisor required in production"
        assert self.sandbox_enabled,             "sandbox cannot be disabled in production"
        assert self.otel_endpoint is not None,   "tracing required in production"
        assert self.ratelimit_enabled,           "rate limiting required in production"

    if self.environment in ("staging", "production"):
        assert self.github_client_id is not None, \
            "GitHub OAuth login required outside local/ci"

    # ── Always, in every environment and tier ─────────────────────────────
    assert self.ff_strict_evidence_binding,  "evidence binding cannot be disabled"
    assert not self.ff_auto_merge,           "auto-merge not permitted before V3"

    if self.service_name == "api":
        assert self.supabase_service_role_key is None, \
            "SECURITY: api service must not hold the service-role key"
        assert self.supabase_jwks_url is not None, \
            "api requires JWKS for RS256 verification"

    return self
```

Three of these deserve emphasis.

**The service-role key assertion.** That key bypasses RLS entirely. It belongs in the worker, which legitimately processes many tenants, and nowhere else. An `api` process holding it would turn a single application-layer authorisation bug into a full cross-tenant breach — so we make that configuration impossible to boot.

**The evaluation-tier GitHub key assertion.** A V1 deployment does not merely *choose* not to write to customer repositories — it structurally **cannot**, because it holds no App private key and therefore cannot mint an installation token. This is what makes the V1 safety claim verifiable rather than aspirational (`A4-ADR-007`).

**`ff_strict_evidence_binding` and `ff_auto_merge` are asserted in every environment**, not just production. Evidence binding is principle P2 and auto-merge is a V3 capability; neither is something a local override should be able to switch off, because a developer who disables evidence binding to "get past" a failing fixture has disabled the primary anti-hallucination control (H1) and will not notice.

### 6.1 Coverage enforcement (A3) — the ratchet

Coverage is configured from day one and **enforced in stages**, because a hard 85% gate against a near-empty Week 1 repository either fails immediately or passes vacuously — neither of which measures anything.

| From | Overall floor | Security-critical floor (auth, RLS, tenancy) | Rationale |
|---|---|---|---|
| Phase 1 (tooling) | measured, **not enforced** | — | Report only; establishes the baseline |
| Phase 2 (schema + RLS) | measured | **≥ 95% enforced** | The RLS suite exists and is the highest-consequence code in the system. It is gated the moment it exists |
| Phase 3 (auth) | measured | **≥ 95% enforced** | Extends to JWT verification and session handling |
| Phase 4 (API foundation) | **≥ 60% enforced** | ≥ 95% | Enough application code exists for an overall number to mean something |
| Phase 6 (ingestion) | **≥ 75%** | ≥ 95% | Fingerprinting joins the ≥95% tier (`14` §10) |
| Phase 10 (sandbox) | **≥ 80%** | ≥ 95% | |
| Phase 15 (eval harness) | **≥ 85%** | ≥ 95% | Final targets from `14` §10 |

The ratchet is **monotonic**: `RT_COVERAGE_MIN_OVERALL` may only ever be raised. Lowering it requires an explicit commit that says why, and CI flags any decrease. Per-area floors from `14` §10 (pipeline ≥90%, fingerprint/retrieval/scoring ≥95%, auth/RLS ≥95%) apply from the phase that introduces each area.

---

*Next: [`A4-ADR-LOG.md`](./A4-ADR-LOG.md)*
