# `fixtures/`

The ground truth the whole system is measured against. Built in **Phase 5**
(T3.1–T3.3), before the pipeline — you cannot test a pipeline without
known-good input, and hand-written payloads are subtly unrealistic in exactly
the ways that make the pipeline look better than it is (`docs/A1` §9).

```
fixtures/
├─ synthetic-repo/     42 files, ~1,780 lines, 52 tests (50 pass, 2 fail on purpose)
├─ triggers/           one reproduction per case — how we know the bugs are real
├─ corpus/             generates the payloads from those reproductions
└─ error-corpus/       25 cases — <case_id>.case.json (ground truth)
                                  <case_id>.json      (the POST /v1/events body)
```

`triggers/` exists because of `A1` §9: *if you can't trigger it by running the
code, it isn't a fixture — it's a fiction.* Each of the 25 cases has a trigger
that executes the synthetic repository and reproduces its defect, so "the bug
is present" is a thing we run rather than a thing we assert. T3.2 captures the
error payloads from those real tracebacks instead of hand-writing them.

It lives **outside** `synthetic-repo/` deliberately. A checkout API does not
ship a directory of scripts that break it on purpose, and retrieval would learn
to find bugs by looking for our annotations rather than by reading the code.

**The payloads are generated, never written by hand** (`uv run python -m
fixtures.corpus.generate`). Every frame, line number and local variable comes
from a traceback the code actually produced. `expected.fingerprint` is `null`
on all 25 until T2.3 implements the real algorithm — a hand-written fingerprint
would be a number the implementation is then forced to match by coincidence.

The two deliberately failing tests are not an oversight: gate **G6** classifies
pre-existing failures as `already_failing` so they do not count against a patch,
and that branch needs to be exercised by something real.

**Controls:** `unfixable-01` and `unfixable-02` must terminate as
`insufficient_context` — no patch, no PR, no fabricated root cause. Metrics M14
(2/2) and M15 (0.00) are merge-blocking. A pipeline that always produces an
answer is worse than one that admits it cannot.

Canonical values for the reference case `null-prop-01` live in
`docs/18-CANONICAL-REGISTRY.md` §7. No other document restates them.
