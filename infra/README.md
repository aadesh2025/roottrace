# `infra/`

Layout is fixed by `docs/13-DEPLOYMENT.md` §4.

```
infra/
├─ terraform/   modules + per-environment stacks, remote locked state
├─ docker/      api · worker · sandbox-python · sandbox-node images
├─ supabase/    migrations (forward-only, numbered) · seed · config.toml
├─ config/      models.yaml — tier routing (docs/A3 §4)
└─ scripts/     deploy · rollback · migrate · warm-wheel-cache
```

**Everything is Terraform-managed. Manual console changes are forbidden** — a
`terraform plan` in CI that shows unexpected drift fails the build, which is how
that rule is enforced rather than merely stated.

Image rules, without exception: base images pinned by **digest** not tag,
multi-stage builds, non-root with a fixed UID, no secrets in any layer, Trivy
scanned with HIGH/CRITICAL blocking.

Populated by ticket: `supabase/migrations` at T1.2 · `docker/` at T1.5 and T6.1
· `config/models.yaml` at T5.1 · `terraform/` at deployment.
