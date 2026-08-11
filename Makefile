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

UV   ?= uv
PNPM ?= pnpm

# Pinned by digest, not tag — a tag can be re-pointed, a digest cannot
# (docs/13 §3). Currently gitleaks v8.30.1. Bump with:
#   docker buildx imagetools inspect zricethezav/gitleaks:<tag> \
#     --format '{{println .Manifest.Digest}}'
GITLEAKS_IMAGE ?= zricethezav/gitleaks@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f

# The coverage ratchet (docs/A3 §6.1). MONOTONIC: this may only ever be raised.
# Lowering it requires a commit that says why. Phase 1 measures without
# enforcing; Phase 4 raises it to 60, Phase 6 to 75, Phase 10 to 80, Phase 15
# to 85. Security-critical areas are gated separately from Phase 2 at 95.
RT_COVERAGE_MIN_OVERALL ?= 0

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
	  --cov-fail-under=$(RT_COVERAGE_MIN_OVERALL)

test-integration: ## pytest -m integration (testcontainers: Postgres + Redis)
	@echo "test-integration: no integration suite until T1.2 (docs/15 §3)."
	@echo "T1.2 adds the migrations, the first integration tests, and"
	@echo "uncomments the integration job in .github/workflows/ci.yml."
	@exit 1

test-security: ## pytest -m security — RLS, sandbox isolation, injection corpus
	@echo "test-security: enabled by T1.3 (RLS suite), extended by T6.3 and T10.2."
	@exit 1

test-e2e: ## playwright test
	@echo "test-e2e: enabled by Phase 16 (T9.*). No dashboard exists yet."
	@exit 1

# ── Local development ──────────────────────────────────────────────────────

dev: ## supabase start · redis · api · worker
	@echo "dev: enabled by T1.5 (API skeleton). Supabase alone: make db-reset."
	@exit 1

db-reset: ## supabase db reset + seed
	@echo "db-reset: enabled by T1.2. infra/supabase/migrations/ is empty."
	@exit 1

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
