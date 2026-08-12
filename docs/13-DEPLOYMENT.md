# 13 — Deployment

> Environments, infrastructure, CI/CD, migrations, rollback, and disaster recovery.

---

## 1. Environments

| Environment | Deployment tier | Purpose | Data | Access | GitHub mode |
|---|---|---|---|---|---|
| `local` | `evaluation` | Development | Fixtures + seeded DB | Developer | `fixture` |
| `ci` | `evaluation` | Automated tests | Ephemeral, per-run | CI only | `fixture` / `replay` |
| `staging` | `evaluation` (V1) → `live` (V2) | Pre-production verification | Anonymised subset | Team | `replay`, then `live` on one canary repo |
| `production` | **`evaluation` in V1** → `live` in V2 | Live service | Customer data | On-call only, audited | `fixture` in V1, `live` in V2 |

**Environment and deployment tier are orthogonal** (`A3` §1.1). `RT_ENVIRONMENT` sets operational rigour — gVisor, tracing, no debug logs, rate limits. `RT_DEPLOYMENT_TIER` sets blast radius — whether this deployment can reach a real repository at all.

That distinction is what makes a **V1 production deployment** coherent: `RT_ENVIRONMENT=production` + `RT_DEPLOYMENT_TIER=evaluation` gives full production rigour while holding no GitHub App private key, so it cannot mint an installation token and therefore cannot write to any customer repository. V2 flips the tier to `live`; nothing else about the deployment changes.

**No shared development database.** Every developer runs their own Supabase local stack. Shared dev databases accumulate incompatible schema drift and turn "works on my machine" into a weekly occurrence.

---

## 2. Infrastructure

### 2.1 Production topology

```
                          ┌──────────────────┐
   Users ────────────────►│  Vercel Edge     │
                          │  (web, Next.js)  │
                          └────────┬─────────┘
                                   │ HTTPS / WSS
                          ┌────────▼─────────┐
   SDKs ─────────────────►│  Load balancer   │
                          └────────┬─────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
           ┌─────────────────┐          ┌─────────────────┐
           │  api  (Fly.io)  │          │  api  (Fly.io)  │
           │  2–8 replicas   │          │                 │
           └────────┬────────┘          └────────┬────────┘
                    └──────────────┬─────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        ▼                          ▼                          ▼
┌───────────────┐        ┌──────────────────┐      ┌──────────────────┐
│ Upstash Redis │        │ Supabase         │      │ Supabase Storage │
│ queue+pubsub  │        │ Postgres+pgvector│      │ blobs            │
└───────┬───────┘        └──────────────────┘      └──────────────────┘
        │
        ▼
┌────────────────────────────────────────┐
│  worker  (Fly.io)  2–12 replicas        │
│  autoscaled on queue depth              │
└───────────────┬────────────────────────┘
                │ docker API over local socket
                ▼
┌────────────────────────────────────────┐
│  SANDBOX HOST POOL                      │
│  dedicated Fly Machines                 │
│  ── isolated from all other workloads ──│
└────────────────────────────────────────┘
```

### 2.2 Sizing

| Component | Instance | Min | Max | Scale trigger |
|---|---|---|---|---|
| `web` | Vercel serverless | — | auto | — |
| `api` | 1 vCPU / 1 GB | 2 | 8 | CPU > 70% or p95 > 150 ms |
| `worker` | 2 vCPU / 4 GB | 2 | 12 | `rt:pipeline` depth > 20/replica |
| `sandbox-host` | 4 vCPU / 8 GB | 1 | 4 | Sandbox concurrency at cap |
| Postgres | Supabase Small → Medium | — | — | CPU > 70% sustained |
| Redis | Upstash pay-per-request | — | — | — |

**Sandbox hosts are physically separate from worker hosts.** They run untrusted code; nothing else should share a kernel with them. This is the isolation boundary that matters most in the whole deployment.

### 2.3 Cost estimate (V1 scale — ~50 active projects)

| Item | Monthly |
|---|---|
| Vercel Pro | $20 |
| Fly.io api (2 × 1 vCPU) | $30 |
| Fly.io worker (2 × 2 vCPU) | $70 |
| Fly.io sandbox host (1 × 4 vCPU) | $60 |
| Supabase Pro | $25 |
| Upstash Redis | $20 |
| Object storage + egress | $15 |
| Observability (Grafana Cloud free → paid) | $50 |
| **Infrastructure** | **~$290** |
| LLM (50 projects × ~30 investigations × $0.32) | ~$480 |
| **Total** | **~$770/month** |

At $99/project/month this is comfortably profitable at 20 paying projects. The dominant variable cost is LLM spend, which is why the token budget in `03` §S5 is the single most important economic control in the system.

---

## 3. Container images

Built as written in **`infra/docker/api.Dockerfile`**, which is authoritative; the sketch below is the shape. Build context is the repository root, since the lockfile and the workspace root `pyproject.toml` live above the Dockerfile.

```dockerfile
# infra/docker/api.Dockerfile — multi-stage, non-root, minimal
FROM python@sha256:<pinned> AS builder
WORKDIR /build
COPY --from=ghcr.io/astral-sh/uv:<pinned> /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY apps/*/pyproject.toml packages/*/pyproject.toml ./…/
RUN uv export --frozen --no-dev --package roottrace-api \
      --no-emit-workspace --no-hashes > requirements.txt \
 && grep -q '^fastapi==' requirements.txt \
 && uv pip install --system --no-cache -r requirements.txt

FROM python@sha256:<pinned>
RUN groupadd -r app && useradd -r -g app -u 10001 app
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
WORKDIR /app
COPY --chown=app:app apps/api ./
USER app
EXPOSE 8000
RUN uvicorn --version && python -c "import roottrace_api.serve"
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
  CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://localhost:8000/health').status_code==200 else 1)"
CMD ["python", "-m", "roottrace_api.serve"]
```

Three corrections from T1.5, each of which produced a working-looking image that was not:

- **`--package roottrace-api` is required.** The workspace root's `dependencies` list is empty — every member is wired in through the `dev` group — so `uv export --no-dev` on the root exported *nothing*, `uv pip install -r` on an empty file succeeded, and the image built cleanly and died at startup with `uvicorn: not found`. The `grep -q '^fastapi=='` and the import smoke test below it are what turn that into a build failure.
- **The entrypoint is `python -m roottrace_api.serve`, not `uvicorn …` directly.** uvicorn emits its first log lines before it builds the app, so with the plain command those lines escape the structlog chain and arrive as unparseable plain text at every boot. `serve.py` validates settings, installs the chain, and only then starts the server — which also means the boot invariants run before the port opens.
- **The Dockerfile lives in `infra/docker/`**, matching §4's tree. §3 previously said `apps/api/Dockerfile`.

Rules for every image:

- Base images pinned by **digest**, not tag. A tag can be re-pointed; a digest cannot. This includes the `uv` installer image: an unpinned installer defeats the point of a locked dependency set, since it is the thing resolving it.
- Multi-stage builds — no build toolchain in the runtime layer.
- Non-root user, fixed UID.
- No secrets in any layer; verified by `docker history` inspection in CI.
- Scanned with Trivy; HIGH/CRITICAL blocks the build.
- Labelled with git SHA, build time, and version.

---

## 4. Infrastructure as code

```
infra/
├─ terraform/
│  ├─ modules/{fly-app,supabase,upstash,dns,monitoring}/
│  ├─ environments/{staging,production}/
│  └─ backend.tf                    # remote state, locked
├─ docker/
│  ├─ api.Dockerfile
│  ├─ worker.Dockerfile
│  ├─ sandbox-python.Dockerfile
│  └─ sandbox-node.Dockerfile
├─ supabase/
│  ├─ migrations/                   # forward-only, numbered
│  ├─ seed/
│  └─ config.toml
└─ scripts/{deploy.sh,rollback.sh,migrate.sh,warm-wheel-cache.sh}
```

Everything is Terraform-managed. **Manual console changes are forbidden** — a `terraform plan` in CI that shows unexpected drift fails the build, which is how the rule is actually enforced rather than merely stated.

---

## 5. CI/CD

### 5.1 Pipeline

```
PR opened
├─ lint            ruff · mypy --strict · eslint · tsc --noEmit         2 min
├─ unit tests      pytest -m unit · vitest                              4 min
├─ integration     pytest -m integration (ephemeral PG + Redis)         6 min
├─ e2e             playwright against a preview deploy                  8 min
├─ security        gitleaks · pip-audit · npm audit · trivy             3 min
├─ ai eval         fixture corpus, 25 cases, all metrics                12 min
├─ sandbox verify  17 isolation checks from 07 §12                      5 min
├─ migration test  apply all migrations to a fresh DB, then seed        2 min
└─ terraform plan  no unexpected drift                                  1 min

merge to main
├─ build + push images (api, worker, sandbox-*)
├─ deploy staging
├─ smoke tests against staging
├─ ⏸ manual approval gate
├─ run migrations against production
├─ deploy production (rolling)
├─ smoke tests against production
└─ tag release, publish notes
```

### 5.2 Deployment strategy

**Rolling with health gates.** New instances must pass `/health/ready` before receiving traffic; old instances drain for 30 s before termination.

Chosen over blue-green because the worker fleet processes a durable queue — a partially rolled worker fleet is harmless (jobs are idempotent and resumable), so the added complexity and cost of maintaining two full environments buys nothing here.

```bash
# infra/scripts/deploy.sh (essentials)
set -euo pipefail
ENV=$1; SHA=$2

./scripts/migrate.sh "$ENV"                     # migrations first, always

fly deploy --app "roottrace-api-$ENV"    --image "$REG/api:$SHA"    --strategy rolling
fly deploy --app "roottrace-worker-$ENV" --image "$REG/worker:$SHA" --strategy rolling

./scripts/smoke.sh "$ENV" || { ./scripts/rollback.sh "$ENV"; exit 1; }
```

### 5.3 Migration safety

Migrations run **before** the new code deploys, which means every migration must be backward compatible with the currently-running version. Destructive changes are three-phase:

```
Release N     add the new column (nullable), write to both old and new
Release N+1   backfill; read from new, still write both
Release N+2   drop the old column
```

Never in one release. This is slower and it is the only approach that permits a rollback at any point without data loss.

---

## 6. Configuration

```python
class Settings(BaseSettings):
    environment: Literal["local","ci","staging","production"]
    deployment_tier: Literal["evaluation","live"] = "evaluation"
    version: str
    log_level: Literal["debug","info","warning","error"] = "info"
    service_name: Literal["api","worker"]

    database_url: PostgresDsn
    database_pool_size: int = 20
    redis_url: RedisDsn

    supabase_url: HttpUrl
    supabase_anon_key: SecretStr
    supabase_service_role_key: SecretStr | None = None    # worker only
    supabase_jwks_url: HttpUrl | None = None              # api only (asymmetric, B12)

    github_app_id: int | None = None
    github_private_key: SecretStr | None = None
    github_webhook_secret: SecretStr | None = None
    github_mode: Literal["fixture","replay","live"] = "fixture"

    llm_config_path: Path = Path("infra/config/models.yaml")
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    sandbox_enabled: bool = True
    sandbox_concurrency: int = 4
    sandbox_timeout_seconds: int = 90          # hard kill; p95 target is 45s (B11)
    sandbox_runtime: Literal["runc","runsc"] = "runsc"

    otel_endpoint: HttpUrl | None = None

    # Full invariant set is canonical in `A3` §6. Summarised here:
    @model_validator(mode="after")
    def boot_invariants(self):
        if self.deployment_tier == "evaluation":
            assert self.github_mode in ("fixture","replay")
            assert self.github_private_key is None, \
                "SECURITY: evaluation tier must not hold a GitHub App key"
        else:
            assert self.github_mode == "live"
            assert self.github_private_key is not None

        if self.environment == "production":
            assert self.log_level != "debug", "debug logging forbidden in production"
            assert self.sandbox_runtime == "runsc", "gVisor required in production"
            assert self.otel_endpoint is not None

        if self.service_name == "api":
            assert self.supabase_service_role_key is None, \
                "SECURITY: api must not hold the service-role key"
            assert self.supabase_jwks_url is not None
        return self

    model_config = SettingsConfigDict(env_prefix="RT_", secrets_dir="/run/secrets")
```

**The process refuses to start on invalid config.** A misconfigured service that boots and behaves subtly wrongly is far worse than one that fails loudly at startup.

### Feature flags

| Flag | Default | Purpose |
|---|---|---|
| `RT_FF_SANDBOX_ENABLED` | true | Kill switch for sandbox validation |
| `RT_FF_AUTO_MERGE` | false | Global auto-merge kill switch |
| `RT_FF_MULTI_MODEL_CONSENSUS` | false | V2 |
| `RT_FF_CI_SECOND_GATE` | false | V2 |
| `RT_FF_REPO_INDEXING` | false | V2 |
| `RT_FF_PROMPT_VERSION_OVERRIDE` | null | A/B prompt evaluation |

Flags are read at request time, not at boot — a bad prompt version or a misbehaving sandbox can be disabled in seconds without a deploy.

---

## 7. Rollback

| Failure | Action | Time |
|---|---|---|
| Bad app deploy | `fly deploy --image <previous-sha>` | ~2 min |
| Bad prompt version | Flip `RT_FF_PROMPT_VERSION_OVERRIDE` | seconds |
| Bad model routing | Edit `models.yaml`, restart workers | ~1 min |
| Bad migration | Forward-fix migration; restore from PITR only as a last resort | 5–30 min |
| Sandbox misbehaving | `RT_FF_SANDBOX_ENABLED=false` — pipeline degrades to syntax-only and says so | seconds |
| Cost runaway | Lower project caps; breakers open automatically | seconds |

```bash
# infra/scripts/rollback.sh
set -euo pipefail
ENV=$1
PREV=$(fly releases --app "roottrace-api-$ENV" --json | jq -r '.[1].version')
fly deploy --app "roottrace-api-$ENV"    --image "$REG/api:$PREV"
fly deploy --app "roottrace-worker-$ENV" --image "$REG/worker:$PREV"
./scripts/smoke.sh "$ENV"
```

**Migrations are never rolled back.** Down-migrations are a trap: they are rarely tested, frequently lossy, and the situation in which you need one is the worst possible time to discover it doesn't work. Forward-fix only.

---

## 8. Disaster recovery

| Scenario | RTO | RPO | Procedure |
|---|---|---|---|
| Single instance failure | 0 | 0 | Load balancer routes around it; autoscaler replaces it |
| Region degradation | 30 min | 5 min | Redeploy to a secondary region; Supabase read replica promoted |
| Database corruption | 1 h | 5 min | Supabase PITR restore |
| Accidental data deletion | 1 h | 5 min | PITR restore to a scratch instance, extract, re-import |
| Redis loss | 5 min | queue only | Queue rebuilt from `raw_events` where `processed_at is null` |
| Object storage loss | 4 h | 24 h | Restore from cross-region replication; source payloads still in Postgres |
| Complete platform loss | 4 h | 5 min | Terraform apply to a new project + PITR restore |

**Redis loss is survivable by design.** The queue is a performance optimisation, not a source of truth — every accepted event is durably in Postgres before it is enqueued, so a lost queue is rebuilt with a single query. This property is why ingest writes to Postgres *before* enqueueing.

### Backup verification

- Automated restore test **weekly**, to an isolated project, with row-count and checksum verification.
- Quarterly manual DR drill against the full runbook.
- A backup that has never been restored is a hypothesis, not a backup.

---

## 9. Runbooks

### Scale up for an expected spike

```
1. fly scale count 6 --app roottrace-api-production
2. fly scale count 10 --app roottrace-worker-production
3. Raise project cost caps if the spike is legitimate
4. Watch queue depth and ingest p99
5. Scale back after 2 h of normal levels
```

### Drain and restart workers safely

```
1. Workers finish the in-flight stage, then stop consuming (SIGTERM handler)
2. Rolling restart — jobs redeliver automatically
3. Verify queue depth returns to baseline
4. Verify no investigation is stuck in a non-terminal state older than 10 min
```

### Emergency: disable all AI processing

```
1. RT_FF_SANDBOX_ENABLED=false     # stop executing generated code
2. Pause the rt:pipeline queue      # stop new investigations
3. Ingest continues — errors are still captured and grouped
4. Post status; explain that analysis is paused, capture is not
```

The ordering matters: stopping analysis while continuing to capture preserves the customer's data during whatever incident prompted the shutdown.

---

## 10. Pre-launch deployment checklist

**Infrastructure**
- [ ] Terraform state remote and locked
- [ ] All resources managed by IaC; `plan` shows zero drift
- [ ] Sandbox hosts on separate machines from workers
- [ ] Postgres PITR enabled and verified by a real restore
- [ ] Object storage lifecycle rules applied
- [ ] DNS + TLS + HSTS preload confirmed

**Deployment**
- [ ] Rolling deploy verified with zero dropped requests
- [ ] Rollback executed successfully in staging
- [ ] Migrations tested forward on a production-sized dataset
- [ ] Smoke tests cover ingest → pipeline → PR
- [ ] Health checks wired to the load balancer

**Configuration**
- [ ] Production settings validators pass
- [ ] All secrets in the platform secret manager, none in env files
- [ ] Feature flags default to safe values
- [ ] `RT_DEPLOYMENT_TIER` correct for the release; `evaluation` deployments verified to hold **no** GitHub App private key
- [ ] `github_mode=live` only where `deployment_tier=live`, verified by boot invariant
- [ ] `RT_SUPABASE_JWKS_URL` set on `api`; `RT_SUPABASE_JWT_SECRET` absent everywhere (retired, B12)

**Observability**
- [ ] Metrics scraped, dashboards live
- [ ] Alerts routed to on-call and tested end to end
- [ ] Trace sampling configured per `12` §4.3
- [ ] Log aggregation with redaction verified

**Operations**
- [ ] Runbooks written and rehearsed
- [ ] On-call rotation defined
- [ ] Status page configured
- [ ] Backup restore verified within the last 7 days

---

*Next: [`14-TESTING.md`](./14-TESTING.md)*
