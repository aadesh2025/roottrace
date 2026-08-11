# `fixtures/`

The ground truth the whole system is measured against. Built in **Phase 5**
(T3.1–T3.3), before the pipeline — you cannot test a pipeline without
known-good input, and hand-written payloads are subtly unrealistic in exactly
the ways that make the pipeline look better than it is (`docs/A1` §9).

```
fixtures/
├─ synthetic-repo/     41 files, ~2,400 lines, 49 tests (47 pass, 2 fail on purpose)
└─ error-corpus/       25 cases — one case, one file: <case_id>.case.json
```

The two deliberately failing tests are not an oversight: gate **G6** classifies
pre-existing failures as `already_failing` so they do not count against a patch,
and that branch needs to be exercised by something real.

**Controls:** `unfixable-01` and `unfixable-02` must terminate as
`insufficient_context` — no patch, no PR, no fabricated root cause. Metrics M14
(2/2) and M15 (0.00) are merge-blocking. A pipeline that always produces an
answer is worse than one that admits it cannot.

Canonical values for the reference case `null-prop-01` live in
`docs/18-CANONICAL-REGISTRY.md` §7. No other document restates them.
