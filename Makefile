# RootTrace AI — canonical developer interface.
#
# Target list is CANONICAL in docs/appendix/A3-CONFIGURATION.md §5.2.
# CI invokes these same targets, so local and CI cannot diverge: a build that
# passes `make check` locally passes the CI check job by construction.
#
# Windows: run from Git Bash, not PowerShell. GNU Make needs to find sh.exe on
# PATH; from Git Bash it does, and recipes then behave exactly as they do in
# CI. SHELL is deliberately not pinned here for the same reason.
#
# Targets that a later phase enables fail loudly and name their ticket. They do
# not quietly succeed — a green no-op is how a missing gate goes unnoticed.

UV       ?= uv
PNPM     ?= pnpm
SUPABASE ?= ./node_modules/.bin/supabase

# Pinned by digest, not tag — a tag can be re-pointed, a digest cannot
# (docs/13 §3). Currently gitleaks v8.30.1. Bump with:
#   docker buildx imagetools inspect zricethezav/gitleaks:<tag> \
#     --format '{{println .Manifest.Digest}}'
GITLEAKS_IMAGE ?= zricethezav/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f

# The coverage ratchet (docs/A3 §6.1). MONOTONIC: this may only ever be raised.
# Lowering it requires a commit that says why. Phase 1 measured without
# enforcing; Phase 4 (T1.5) raises it to 60, Phase 6 to 75, Phase 10 to 80,
# Phase 15 to 85. Security-critical areas are gated separately from Phase 2 at
# 95.
#
# Renamed out of the `RT_` prefix, for the reason given below the next block.
# CI sets this as a job-level `env:`, which exports it into every process, and
# the unrecognised-RT_* boot invariant then refused to start the app in every
# test that constructs Settings from the environment. It failed only in CI,
# because `make` passes its own variables as make variables, not environment
# ones.
ROOTTRACE_COVERAGE_MIN_OVERALL ?= 60

# The security-critical floor from docs/A3 §6.1, enforced from Phase 2. Applies
# to auth, RLS and tenancy code. Also monotonic.
ROOTTRACE_COVERAGE_MIN_SECURITY ?= 95

# The Supabase admin key, read out of the running stack at call time and passed
# to the integration suite. It is deliberately not a literal anywhere in the
# repository: a key pasted into a test file is a real credential in git, and
# allowlisting that file in .gitleaks.toml would be the fail-open version of the
# same mistake — the next key pasted there would be ignored too.
#
# NOT prefixed `RT_`. That namespace belongs to the Settings model, and the
# unrecognised-RT_* boot invariant rejects anything in it without a matching
# field — correctly, since it cannot tell a test harness variable from a stale
# application one. Harness variables live outside the prefix.
SUPABASE_SECRET_CMD = $(SUPABASE) status --workdir infra -o env \
  | sed -n 's/^SECRET_KEY="\(.*\)"$$/\1/p'

.DEFAULT_GOAL := help
.PHONY: help bootstrap check fmt fmt-check lint typecheck \
        test-unit test-integration test-security test-e2e \
        dev db-reset fixtures-reset fixture-run fixtures-verify \
        eval eval-compare audit ci

# ── Meta ───────────────────────────────────────────────────────────────────

help: ## Show every target
	@echo "RootTrace AI — make targets (canonical: docs/A3 §5.2)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

bootstrap: ## Install every toolchain dependency (run once per clone)
	$(UV) sync --all-groups
	$(PNPM) install --frozen-lockfile
	$(UV) run pre-commit install
	@command -v gitleaks >/dev/null 2>&1 || { \
	  echo ""; \
	  echo "  WARNING: gitleaks is not on PATH. The pre-commit secret scan will"; \
	  echo "  fail until it is installed (deliberately — see .pre-commit-config.yaml):"; \
	  echo "    winget install Gitleaks.Gitleaks --version 8.30.1"; \
	  echo "    brew install gitleaks   |   apt-get install gitleaks"; \
	  echo ""; }

# ── The pre-push gate ──────────────────────────────────────────────────────

check: fmt-check lint typecheck test-unit ## fmt-check → lint → typecheck → test-unit
	@echo "✓ check passed"

fmt: ## Format Python and TypeScript
	$(UV) run ruff format .
	$(PNPM) run fmt

fmt-check: ## Fail on unformatted code
	$(UV) run ruff format --check .
	$(PNPM) run fmt:check

lint: ## ruff check · eslint
	$(UV) run ruff check .
	$(PNPM) run lint

typecheck: ## mypy --strict · tsc --noEmit
	$(UV) run mypy apps packages
	$(PNPM) run typecheck

# ── Tests ──────────────────────────────────────────────────────────────────

test-unit: ## pytest -m unit (+ vitest from Phase 16)
	$(UV) run pytest -m unit \
	  --cov --cov-report=term-missing \
	  --cov-fail-under=$(ROOTTRACE_COVERAGE_MIN_OVERALL)

test-integration: ## pytest -m integration (against the local Supabase stack)
	@# Runs against the Supabase-managed Postgres, not a bare testcontainer: the
	@# schema depends on Supabase roles and the auth schema, which a stock
	@# Postgres image does not have. Deviation recorded in docs/14 §4.
	@$(SUPABASE) status --workdir infra >/dev/null 2>&1 || { \
	  echo "test-integration: Supabase is not running. Start it with:"; \
	  echo "    make db-reset"; \
	  exit 1; }
	ROOTTRACE_TEST_ADMIN_KEY="$$($(SUPABASE_SECRET_CMD))" $(UV) run pytest -m integration

test-security: ## pytest -m security — RLS, sandbox isolation, injection corpus
	@$(SUPABASE) status --workdir infra >/dev/null 2>&1 || { \
	  echo "test-security: Supabase is not running. Start it with: make db-reset"; \
	  exit 1; }
	ROOTTRACE_TEST_ADMIN_KEY="$$($(SUPABASE_SECRET_CMD))" $(UV) run pytest -m security
	@# The >=95% security-critical floor (docs/A3 §6.1), which applies to auth,
	@# RLS and tenancy code from the phase that introduces each.
	@#
	@# The detection pattern was `auth*.py`, which matches a FILE called
	@# auth.py. T1.4 shipped a `auth/` PACKAGE, so the glob missed it and the
	@# gate stayed off through the entire ticket it was written to guard —
	@# green, and proving nothing. Fixed to `auth/*.py` and reduced to a single
	@# subject, because `ls a b` exits non-zero when EITHER is missing: pairing
	@# it with a not-yet-existing worker path would have kept it off anyway.
	@# Add the worker's tenancy module here as a second `||` clause when
	@# TenantRepository lands.
	@#
	@# Scoped to the auth package, not the whole api package: a 95% floor spread
	@# across unrelated modules is not the control A3 §6.1 describes, and would
	@# fail for reasons with nothing to do with auth.
	@#
	@# `-m "security or unit"` because the auth unit tests carry the `unit`
	@# marker. Measuring auth coverage from the security suite alone reports a
	@# number far below the truth and gates on noise.
	@if ls apps/api/roottrace_api/auth/*.py >/dev/null 2>&1; then \
	  ROOTTRACE_TEST_ADMIN_KEY="$$($(SUPABASE_SECRET_CMD))" $(UV) run pytest -m "security or unit" \
	    --cov=apps/api/roottrace_api/auth \
	    --cov-fail-under=$(ROOTTRACE_COVERAGE_MIN_SECURITY); \
	else \
	  echo "security coverage gate: no Python auth modules yet — enabled by T1.4."; \
	fi

test-e2e: ## playwright test
	@echo "test-e2e: enabled by Phase 16 (T9.*). No dashboard exists yet."
	@exit 1

# ── Local development ──────────────────────────────────────────────────────

dev: ## supabase start · redis · api · worker
	@echo "dev: enabled by T1.5 (API skeleton). Supabase alone: make db-reset."
	@exit 1

db-reset: ## supabase db reset + seed (starts the stack if it is down)
	@$(SUPABASE) status --workdir infra >/dev/null 2>&1 \
	  && $(SUPABASE) db reset --workdir infra \
	  || $(SUPABASE) start --workdir infra

# ── Fixtures ───────────────────────────────────────────────────────────────

fixtures-reset: ## Rebuild the fixture DB and load the synthetic repo
	@echo "fixtures-reset: enabled by T3.1 (docs/15 §5)."
	@exit 1

fixture-run: ## Run one fixture case end to end — make fixture-run CASE=null-prop-01
	@echo "fixture-run: enabled once the pipeline exists (Phase 6 onward)."
	@exit 1

fixtures-verify: ## Assert every ground-truth path/symbol/line resolves to real code
	@echo "fixtures-verify: enabled by T3.2 (docs/15 §5)."
	@exit 1

# ── Evaluation ─────────────────────────────────────────────────────────────

eval: ## Full 25-case corpus × 3 runs, all metrics
	@echo "eval: enabled by T10.1 (docs/15 §12). Needs the full pipeline."
	@exit 1

eval-compare: ## Paired baseline-vs-candidate — make eval-compare BASELINE=v3 CANDIDATE=v4
	@echo "eval-compare: enabled by T10.1 (docs/15 §12)."
	@exit 1

# ── Security ───────────────────────────────────────────────────────────────

audit: ## gitleaks (FULL history) · pip-audit · action-pin check
	@# SC29a (docs/11 §13.3): every third-party action pinned by 40-hex commit
	@# SHA. A tag is mutable, and whoever can move it runs arbitrary code in CI
	@# with our repository checked out.
	@if grep -rnE '^\s*-?\s*uses:' .github/workflows 2>/dev/null \
	     | grep -vE '@[0-9a-f]{40}(\s|$$)'; then \
	  echo "audit: the actions above are not pinned to a commit SHA (SC29a)."; \
	  exit 1; \
	fi
	@echo "audit: all GitHub Actions are SHA-pinned (SC29a)"
	@# The pre-commit gitleaks hook scans the STAGED DIFF only
	@# (`--staged`, pass_filenames: false), which is right for a commit hook and
	@# useless as an audit: it would report clean on a repo whose history is
	@# full of keys. SC29 requires full history, so this runs the CLI over the
	@# whole object graph instead. Digest-pinned, per docs/13 §3.
	@# Native binary first, digest-pinned image as the fallback. Both are
	@# gitleaks 8.30.1. If neither is available this FAILS — it never degrades
	@# into a scan that quietly does nothing.
	@if command -v gitleaks >/dev/null 2>&1; then \
	  gitleaks git . --config .gitleaks.toml --redact --verbose; \
	elif docker info >/dev/null 2>&1; then \
	  MSYS_NO_PATHCONV=1 docker run --rm \
	    -v "$$(pwd -W 2>/dev/null || pwd)":/repo -w /repo $(GITLEAKS_IMAGE) \
	    git /repo --config /repo/.gitleaks.toml --redact --verbose; \
	else \
	  echo "audit: no gitleaks binary and no Docker daemon."; \
	  echo "  winget install Gitleaks.Gitleaks --version 8.30.1"; \
	  echo "Refusing to skip the secret scan — a scan that does not run must"; \
	  echo "never look like a scan that found nothing."; \
	  exit 1; \
	fi
	@# Audit the LOCKFILE, not the environment. Auditing the environment trips
	@# over the four workspace members: they are installed editable and do not
	@# exist on PyPI, and --strict fails on any skipped distribution. Exporting
	@# with --no-emit-workspace audits exactly the third-party set that is
	@# actually resolved, and keeps --strict meaningful.
	$(UV) export --frozen --all-groups --no-emit-workspace --no-hashes -o .audit-requirements.txt
	@$(UV) run pip-audit --strict -r .audit-requirements.txt; \
	  status=$$?; rm -f .audit-requirements.txt; exit $$status

# ── CI ─────────────────────────────────────────────────────────────────────

ci: check audit ## Everything CI runs, in CI order. Reproduces a CI failure locally
	@echo "✓ ci passed (Phase 1 job set: check, security)"
