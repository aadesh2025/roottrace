# A1 — Fake Data Fixtures

> The synthetic repository and error corpus that V1 is built and proven against.

---

## 1. Design requirements

The fixtures must be realistic enough that passing on them means something. A toy repo would let V1 pass tests that a real repository would fail.

| Requirement | Why |
|---|---|
| Layered architecture (routes → services → clients → models) | Call-graph traversal must cross real module boundaries |
| Real third-party dependencies | Dependency resolution and import checks must be exercised |
| An existing, **imperfect** test suite | Two tests fail before any patch, so gate G6's baseline classification is exercised |
| Simulated git history with blame | Retrieval strategy D and "introduced by" attribution need real data |
| Release tags with meaningful diffs | Release correlation is one of our strongest signals |
| Bugs actually present in the code | The AI must find real defects, not annotations claiming a defect exists |
| A range of difficulty | Easy cases prove the happy path; hard cases prove we don't over-claim |
| Genuinely unfixable cases | Prove the system abstains honestly rather than always producing something |

---

## 2. Repository structure

```
fixtures/synthetic-repo/                    # "acme/checkout-api"
├─ api/
│  ├─ routes/
│  │  ├─ checkout.py          POST /api/v2/checkout
│  │  ├─ cart.py              cart CRUD
│  │  ├─ webhooks.py          Stripe webhook receiver
│  │  └─ export.py            CSV export endpoint
│  ├─ middleware/
│  │  ├─ auth.py
│  │  └─ rate_limit.py
│  └─ deps.py
├─ services/
│  ├─ checkout.py             ← 4 fixture bugs live here
│  ├─ cart.py
│  ├─ inventory.py            ← race condition bug
│  ├─ pricing.py
│  ├─ quote.py                ← same latent defect as checkout (out-of-scope caller)
│  └─ export.py               ← memory-growth bug
├─ clients/
│  ├─ tax_client.py           ← the canonical fixture bug
│  ├─ inventory_client.py
│  ├─ payment_client.py
│  └─ errors.py               where exceptions are conventionally defined
├─ models/
│  ├─ cart.py
│  ├─ order.py
│  ├─ user.py
│  └─ config.py
├─ config/
│  ├─ settings.py
│  └─ regions.py
├─ tests/
│  ├─ conftest.py             stubbed clients; the suite never touches a network
│  ├─ test_checkout.py        12 tests
│  ├─ test_cart.py            9 tests
│  ├─ test_inventory.py       8 tests
│  ├─ test_pricing.py         11 tests
│  ├─ test_quote.py           3 tests  ← the contract `regression-02` breaks
│  ├─ test_webhooks.py        5 tests, 1 FAILING before any patch
│  └─ test_export.py          4 tests, 1 FAILING before any patch
├─ requirements.txt
├─ pyproject.toml
└─ .roottrace-fixture.json    simulated git metadata
```

**42 files · ~1,780 lines · 52 tests (50 passing, 2 pre-existing failures).**

> **Corrected in T3.1.** This section previously listed six test files summing to exactly 49 tests, and §5 simultaneously required `tests/test_quote.py::test_estimate_with_missing_tax` for `regression-02` — a file the tree did not contain. The file is real now and the counts include it.
>
> The line total is ~1,780, not the ~2,400 originally estimated. Reported rather than padded: the figure that matters is whether retrieval has to cross real module boundaries under a 24,000-token budget, and 39 modules across seven layers does that. Writing filler to reach a round number would make the corpus look harder than it is.

The two pre-existing failures matter: they force gate G6 to run a pre-patch baseline and classify them as `already_failing`. Without them, a repo with any broken test would fail every validation forever, and we'd never notice the bug in our own gate logic.

**Both are deliberately unrelated to any of the 25 cases** (`test_export.py::test_header_includes_created_at`, `test_webhooks.py::test_event_summary_reports_livemode`). If a baseline failure were tied to a case, fixing that case would flip it to passing and corrupt G6's accounting — the suite would then be measuring our own bookkeeping rather than the patch. `tests/integration/test_fixture_repo.py` asserts both the identity of the two failures and their independence from the corpus.

`services/quote.py` calls `TaxClient.get_rate` with the same missing guard as `checkout.py`. The correct behaviour is to fix the client, note that `quote.py` is affected, and **not** expand scope to fix it. This tests scope discipline directly.

---

## 3. Simulated git metadata

```jsonc
// .roottrace-fixture.json
{
  "default_branch": "main",
  "head_sha": "9f2b1c4e8a7d6b5c4a3f2e1d0c9b8a7f6e5d4c3b",
  "releases": [
    { "tag": "v2.14.1", "sha": "3c1a...", "date": "2026-07-18T10:00:00Z" },
    { "tag": "v2.14.2", "sha": "6d4b...", "date": "2026-07-24T09:30:00Z" },
    { "tag": "v2.14.3", "sha": "9f2b...", "date": "2026-08-01T10:00:00Z" }
  ],
  "commits": [
    {
      "sha": "8a3f1c2e5b4d3a2f1e0d9c8b7a6f5e4d3c2b1a09",
      "message": "refactor: extract tax lookup into TaxClient",
      "author": { "name": "Dana Reyes", "email": "dana@acme.io" },
      "date": "2026-07-25T11:04:00Z",
      "files": ["clients/tax_client.py", "services/checkout.py", "services/quote.py"],
      "diff_url": "fixtures/synthetic-repo/.history/8a3f1c2.diff"
    }
  ],
  "blame": {
    "clients/tax_client.py": [
      { "lines": [38, 43], "sha": "8a3f1c2e", "author": "dana@acme.io",
        "date": "2026-07-25T11:04:00Z" },
      { "lines": [1, 37],  "sha": "3c1a9d2f", "author": "sam@acme.io",
        "date": "2026-07-18T09:12:00Z" }
    ]
  }
}
```

The fixture GitHub client serves blame, compare, and commit-history requests from this file, so retrieval strategy D exercises exactly the same code path it will in `live` mode.

---

## 4. The canonical bug (case `null-prop-01`)

The example used throughout the documentation.

```python
# clients/tax_client.py — THE DEFECT
class TaxClient:
    def __init__(self, base_url: str, timeout: float = 2.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def get_rate(self, region: str) -> Decimal | None:
        try:
            resp = self._client.get("/rate", params={"region": region})
            resp.raise_for_status()
            return Decimal(resp.json()["rate"])
        except httpx.HTTPStatusError:
            logger.warning("tax service returned an error for region=%s", region)
            return None                    # ← line 43. Swallows 5xx, returns None.
```

> The defect occupies **lines 38–43** — the request through the swallowed return — as registered in `18` §7, which is authoritative. This snippet previously annotated the return as line 41, which contradicted that range; the code is written to the registry and `tests/integration/test_fixture_repo.py` asserts it line by line, because every document quotes these numbers and the evaluator compares the model's citation against them literally.

```python
# services/checkout.py — WHERE IT SURFACES
def calculate_total(self, cart: Cart, user: User) -> Decimal:
    base_price = cart.subtotal()
    tax_amount = self.tax_client.get_rate(cart.region)   # line 138 — no guard
    subtotal = base_price + tax_amount                    # line 142 — TypeError
    return subtotal
```

```python
# services/quote.py — THE OUT-OF-SCOPE CALLER
def estimate_total(self, cart: Cart) -> Decimal:
    rate = self.tax_client.get_rate(cart.region)          # same latent defect
    return cart.subtotal() * (Decimal("1") + (rate or Decimal("0")))
```

**Before commit `8a3f1c2`,** the inline code raised. The refactor converted that `raise` into a `return None` and did not update either caller. That commit is the root cause, and it is discoverable through blame, through the release diff, and through the code itself — three independent paths, which is what makes this a good baseline case.

### Ground truth

> **Schema authority.** The fixture case schema is canonical in `14` §6.2. This appendix describes the *corpus* — which bugs exist, why each was chosen, what each proves. It does not redefine the schema, and the two must never drift. Each case lives in exactly one file: `fixtures/error-corpus/<case_id>.case.json`.

```jsonc
{
  "case_id": "null-prop-01",
  "schema_version": 1,
  "difficulty": "medium",
  "category": "null_propagation",
  "repository": { "fixture_path": "fixtures/synthetic-repo", "ref": "v2.14.3",
                  "commit_sha": "9f2b1c4e8a7d6b5c4a3f2e1d0c9b8a7f6e5d4c3b" },
  "bug_description": "TaxClient.get_rate catches HTTPStatusError and returns None on any non-200, so a 503 yields None where a Decimal is expected.",
  "api_event": "fixtures/error-corpus/null-prop-01.json",
  "expected": {
    "fingerprint": "a3f8b2c1d4e5f6a7b8c9d0e1f2a3b4c5",
    "issue_error_type": "TypeError",
    "relevant_files": ["clients/tax_client.py", "services/checkout.py",
                       "api/routes/checkout.py"],
    "retrieval_quality_min": 0.60,
    "requires_breadcrumb_signal": true,
    "root_cause_file": "clients/tax_client.py",
    "root_cause_function": "get_rate",
    "root_cause_line_range": [38, 43],
    "root_cause_category": "unhandled_error_path",
    "introduced_by_commit": "8a3f1c2e",
    "evidence_must_cite": [
      { "kind": "file", "repo_path": "clients/tax_client.py", "line_range": [38, 43] },
      { "kind": "breadcrumb", "index": 1 }
    ],
    "must_modify_files": ["clients/tax_client.py"],
    "may_modify_files": ["services/checkout.py", "clients/errors.py",
                         "tests/test_checkout.py"],
    "must_not_modify_files": ["api/routes/checkout.py", "services/quote.py",
                              ".github/workflows/ci.yml", "requirements.txt"],
    "acceptable_fix_strategies": ["raise_typed_exception", "explicit_fallback_policy"],
    "unacceptable_fix_strategies": ["silent_default_zero", "broad_try_except",
                                    "suppress_at_call_site"],
    "requires_regression_test": true,
    "expected_blast_radius_mentions": ["services/quote.py::estimate_total"],
    "validation": { "should_pass": true, "max_attempts": 1, "mode": "full" },
    "confidence_band": "high",
    "final_status": "awaiting_decision",
    "should_open_pr": true
  }
}
```

`silent_default_zero` is listed as unacceptable deliberately. `if tax_amount is None: tax_amount = 0` makes the error vanish, makes every test pass, and silently under-charges every customer during a tax-service outage. It is a "successful" fix by every mechanical measure and a serious financial defect in reality. The evaluator checks for it explicitly, because it is exactly what a shallow diagnosis produces.

---

## 5. Corpus overview

| ID | Category | Difficulty | Root cause | Tests |
|---|---|---|---|---|
| `null-prop-01` | Null propagation | medium | `tax_client.py::get_rate` returns None on 5xx | Baseline; breadcrumb signal |
| `null-prop-02` | Null propagation | easy | `cart.py::get_item` returns None for a missing SKU | Simple guard |
| `null-prop-03` | Null propagation | medium | Optional config value unhandled | Config path |
| `null-prop-04` | Null propagation | hard | Null originates 3 call frames upstream | Deep graph traversal |
| `type-mismatch-01` | Type mismatch | medium | `str` vs `Decimal` across a module boundary | Contract drift |
| `type-mismatch-02` | Type mismatch | medium | Dict returned where a dataclass is expected | Type inference |
| `type-mismatch-03` | Type mismatch | hard | Generic type variance in a shared helper | Hard reasoning |
| `key-error-01` | Missing key | easy | Webhook payload lacks `signature` | Shape assumption |
| `key-error-02` | Missing key | easy | Optional field accessed directly | Simple |
| `key-error-03` | Missing key | medium | Nested access with a partially-present structure | Multi-level |
| `external-01` | External dep | **hard** | Inventory service timeout, no retry | Breadcrumb-critical |
| `external-02` | External dep | **hard** | Payment 429, no backoff | Rate-limit handling |
| `external-03` | External dep | **hard** | DNS failure, no circuit breaker | Resilience |
| `race-01` | Concurrency | **hard** | Inventory decrement without a lock | Concurrency reasoning |
| `race-02` | Concurrency | **hard** | Shared mutable cache across requests | Subtle |
| `boundary-01` | Off-by-one | easy | Pagination `offset` off by one | Simple |
| `boundary-02` | Off-by-one | easy | Slice excludes the last element | Simple |
| `config-01` | Configuration | medium | Region missing from the config map | Config retrieval |
| `config-02` | Configuration | medium | Env var absent in one environment only | Environment-specific |
| `regression-01` | Regression | medium | Signature change in `v2.14.2` | Release correlation |
| `regression-02` | Regression | medium | **First patch attempt breaks an existing test** | **Repair loop** |
| `regression-03` | Regression | hard | Behaviour change without a signature change | Subtle |
| `resource-01` | Resource leak | hard | Unbounded accumulation in the export loop | Memory growth |
| `unfixable-01` | **Control** | — | External service down, handling is already correct | **Must abstain** |
| `unfixable-02` | **Control** | — | Infrastructure DNS failure, not a code defect | **Must abstain** |

### Cases that carry specific weight

**`regression-02`** is the repair-loop test. The obvious first patch breaks `tests/test_quote.py::test_estimate_with_missing_tax`, which asserts the old contract. Gate G6 catches it, the repair loop routes with the failure detail, and attempt 2 succeeds. This is the case E7 in the E2E suite exercises.

**`external-01`** cannot be solved from the stack trace alone. The trace points at a generic timeout handler; the breadcrumbs show a 30-second inventory call preceding it. If retrieval or reasoning ignores breadcrumbs, this case fails — which is exactly what we want it to detect.

**`unfixable-01` and `unfixable-02`** are the honesty check, and their expected behaviour is stated exactly rather than loosely:

| Must | Must not |
|---|---|
| Terminate as `insufficient_context` | Produce a patch of any kind |
| Explain plainly that the trigger is external and the handling is already correct | Open a PR, draft or otherwise |
| Record the retrieval that was performed, so the abstention is inspectable | Fabricate a root cause to fill the field |
| Surface prominently in the dashboard, not hidden as a failure | Report `confidence` above the `insufficient` band |

Measured by **M14 (abstention correctness, 2/2)** and **M15 (false-fabrication rate, 0.00)** in `14` §6.3, both merge-blocking. A system that produces a confident fix for a bug that isn't in the repository is worse than one that admits it can't help — and no accuracy gain elsewhere compensates for failing these two.

---

## 6. Error payload example

```jsonc
// fixtures/error-corpus/null-prop-01.json
{
  "events": [{
    "event_id": "evt_fixture_null_prop_01",
    "timestamp": "2026-08-04T09:14:22.481Z",
    "environment": "production",
    "service": "checkout-api",
    "release": "v2.14.3",
    "level": "error",
    "error": {
      "type": "TypeError",
      "message": "unsupported operand type(s) for +: 'decimal.Decimal' and 'NoneType'",
      "stack_trace": "Traceback (most recent call last):\n  File \"/app/api/routes/checkout.py\", line 58, in create_checkout\n    total = checkout_service.calculate_total(cart, user)\n  File \"/app/services/checkout.py\", line 142, in calculate_total\n    subtotal = base_price + tax_amount\nTypeError: unsupported operand type(s) for +: 'decimal.Decimal' and 'NoneType'",
      "stack_frames": [
        { "file": "/app/services/checkout.py", "line": 142, "function": "calculate_total",
          "in_app": true,
          "context_line": "    subtotal = base_price + tax_amount",
          "pre_context": ["    base_price = cart.subtotal()",
                          "    tax_amount = self.tax_client.get_rate(cart.region)",
                          ""],
          "post_context": ["    return subtotal", ""],
          "vars": { "base_price": "Decimal('49.99')", "tax_amount": "None",
                    "cart": "<Cart id=c_8821 region='eu-west'>" } },
        { "file": "/app/api/routes/checkout.py", "line": 58, "function": "create_checkout",
          "in_app": true,
          "context_line": "    total = checkout_service.calculate_total(cart, user)" }
      ]
    },
    "request": {
      "method": "POST", "url": "/api/v2/checkout",
      "route_pattern": "/api/v2/checkout",
      "status_code": 500, "duration_ms": 412,
      "headers": { "content-type": "application/json" },
      "body_sample": "{\"cart_id\":\"c_8821\"}"
    },
    "runtime": { "language": "python", "language_version": "3.12.4",
                 "framework": "fastapi", "framework_version": "0.111.0",
                 "os": "linux", "hostname": "checkout-api-7d9f-x4k2" },
    "user_context": { "user_hash": "u_9f2b1c", "plan": "pro", "is_authenticated": true },
    "breadcrumbs": [
      { "ts": "2026-08-04T09:14:22.101Z", "category": "db",
        "message": "SELECT * FROM carts WHERE id=? (12ms)", "level": "info" },
      { "ts": "2026-08-04T09:14:22.340Z", "category": "http",
        "message": "GET tax-service/rate?region=eu-west → 503", "level": "warning" }
    ],
    "tags": { "region": "eu-west-1", "tenant_tier": "enterprise" },
    "extra": { "cart_item_count": 3, "cart_subtotal": "49.99" }
  }]
}
```

The `503` breadcrumb at T−141ms is the decisive evidence. It is the piece of information that separates a correct diagnosis from a plausible guess, and it exists nowhere in the stack trace.

---

## 7. Path mappings

```jsonc
{
  "path_mappings": [
    { "from": "/app/", "to": "" }
  ],
  "root_path": "",
  "service_map": { "checkout-api": "" }
}
```

Three of the 25 fixtures deliberately use non-standard prefixes (`/usr/src/app/`, `/workspace/services/`, `C:\build\app\`) to exercise the heuristic and suffix-matching branches of the resolution cascade.

---

## 8. Using the fixtures

```bash
# Reset the fixture database and load the synthetic repo
make fixtures-reset

# Run one case end to end
make fixture-run CASE=null-prop-01

# Run the full evaluation corpus (25 × 3 runs)
make eval

# Compare a candidate prompt version against the current baseline
make eval-compare BASELINE=reason.v3 CANDIDATE=reason.v4

# Verify every ground-truth reference resolves to real code
make fixtures-verify
```

`make fixtures-verify` runs in CI. It confirms every ground-truth file path, function name, and line range points at real code in the synthetic repo, so a refactor of the fixtures cannot silently invalidate the evaluation harness.

---

## 9. Adding a new fixture case

1. Write the bug into the synthetic repo, in a place where it is genuinely reachable.
2. Add a commit entry to `.roottrace-fixture.json` with blame ranges.
3. Add or update a test that covers the affected code (or deliberately don't, to test the no-coverage path).
4. Produce the error payload by actually triggering the bug — never hand-write a stack trace. Hand-written traces are subtly unrealistic in ways that make the pipeline look better than it is.
5. Write the ground truth, including `unacceptable_fix_strategies`.
6. Run `make fixtures-verify`.
7. Run the case three times and record baseline metrics.

Rule: **every fixture bug must be real.** If you can't trigger it by running the code, it isn't a fixture — it's a fiction, and a pipeline that passes on fiction tells you nothing.

---

*Next: [`A2-PROMPT-LIBRARY.md`](./A2-PROMPT-LIBRARY.md)*
