# 09 — Frontend & Dashboard

> Every page, every panel, every state. Built on the design system in `10-DESIGN-SYSTEM.md`.

---

## 1. Application structure

```
apps/web/
├─ app/
│  ├─ (auth)/
│  │  ├─ login/page.tsx
│  │  ├─ callback/page.tsx
│  │  └─ onboarding/page.tsx
│  ├─ (dashboard)/
│  │  ├─ layout.tsx                       # sidebar + top bar shell
│  │  ├─ page.tsx                         # Overview
│  │  ├─ issues/
│  │  │  ├─ page.tsx                      # Issue list
│  │  │  └─ [issueId]/page.tsx            # Issue detail
│  │  ├─ investigations/
│  │  │  ├─ page.tsx                      # Investigation list
│  │  │  └─ [id]/
│  │  │     ├─ page.tsx                   # Investigation detail (tabbed)
│  │  │     ├─ pipeline/page.tsx
│  │  │     ├─ evidence/page.tsx
│  │  │     ├─ patch/page.tsx
│  │  │     ├─ sandbox/page.tsx
│  │  │     └─ raw/page.tsx
│  │  ├─ logs/
│  │  │  ├─ page.tsx                      # Log explorer
│  │  │  └─ [eventId]/page.tsx
│  │  ├─ analytics/
│  │  │  ├─ page.tsx
│  │  │  ├─ repeats/page.tsx
│  │  │  ├─ pipeline/page.tsx
│  │  │  └─ cost/page.tsx
│  │  └─ settings/
│  │     ├─ general/page.tsx
│  │     ├─ repositories/page.tsx
│  │     ├─ api-keys/page.tsx
│  │     ├─ ai/page.tsx
│  │     ├─ members/page.tsx
│  │     └─ audit/page.tsx
│  └─ api/                                 # BFF route handlers only
├─ components/
│  ├─ ui/                                  # shadcn primitives, restyled
│  ├─ pipeline/                            # StageNode, PipelineViewer, StageDetail
│  ├─ evidence/                            # CitationCard, CodeExcerpt, ReasoningChain
│  ├─ diff/                                # DiffViewer, FileTree, HunkNav
│  ├─ charts/                              # TimeSeries, Sparkline, Distribution
│  ├─ confidence/                          # ConfidenceMeter, BandBadge, Breakdown
│  └─ sandbox/                             # Console, GateList, GateDetail
├─ lib/
│  ├─ api-client.ts                        # single snake→camel boundary
│  ├─ ws-client.ts                         # reconnecting WebSocket
│  ├─ query-keys.ts
│  └─ formatters.ts
└─ styles/globals.css                      # design tokens
```

### Rendering strategy

| Page | Strategy | Why |
|---|---|---|
| Overview | RSC + client islands | Heavy aggregate queries; only the live feed needs interactivity |
| Issue list | RSC with searchParams | Filters are URL state — shareable, back-button correct |
| Issue detail | RSC shell + client chart | Chart needs interaction; the rest is static |
| Investigation detail | RSC shell + **client pipeline** | Live WebSocket state |
| Log explorer | RSC + client virtual list | 100k rows demand virtualisation |
| Analytics | RSC, cached 60s | Expensive, tolerates staleness |
| Settings | Client | Forms |

State management: **TanStack Query** for server state, `nuqs` for URL state, React Context for the WebSocket connection only. No Redux, no Zustand — the app has almost no genuine client state.

---

## 2. Overview page — `/`

The first thing a user sees. It must answer three questions in under five seconds: *Is anything on fire? What is the AI doing right now? Is it working?*

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Overview                                          Last 24 hours  ▾        │
│  checkout-api · production                                                 │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐       │
│  │ ERRORS       │ │ OPEN ISSUES  │ │ FIXES OPENED │ │ TIME SAVED   │       │
│  │              │ │              │ │              │ │              │       │
│  │   8,421      │ │     34       │ │     12       │ │   18.5h      │       │
│  │   ↓ 12%      │ │   ↑ 3 new    │ │   7 merged   │ │  this week   │       │
│  │ ▁▃▅▂▇▄▃▅▂▁   │ │ ▁▂▃▃▄▄▅▅▅▆   │ │ ▁▁▂▃▂▄▃▅▄▆   │ │              │       │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘       │
│                                                                            │
│  ┌──────────────────────────────────────────┐ ┌─────────────────────────┐  │
│  │  Error volume                            │ │  Live pipeline          │  │
│  │                                          │ │                         │  │
│  │   500┤          ╭─╮                      │ │  ● inv_01J2K9  P1       │  │
│  │      │      ╭───╯ ╰──╮      ╭╮           │ │    TypeError · checkout │  │
│  │   250┤  ╭───╯        ╰──────╯╰───        │ │    ▸▸▸▸▸▸○○○○  reason   │  │
│  │      │──╯                                │ │    42s elapsed          │  │
│  │     0└────┬────┬────┬────┬────┬────      │ │                         │  │
│  │        00  04   08   12   16   20        │ │  ● inv_01J2K7  P2       │  │
│  │                                          │ │    KeyError · webhook   │  │
│  │      ▲ v2.14.3 deployed  ▲ PR #482       │ │    ▸▸▸▸▸▸▸▸▸▸  done     │  │
│  └──────────────────────────────────────────┘ │    0.91 high  PR #481   │  │
│                                                │                         │  │
│  ┌──────────────────────────────────────────┐ │  ○ inv_01J2K5  P2       │  │
│  │  Needs your review                   3   │ │    queued               │  │
│  ├──────────────────────────────────────────┤ │                         │  │
│  │ 0.84 high  TypeError  services/checkout  │ │  ─────────────────────  │  │
│  │            PR #482 · 2h ago      Review →│ │  View all →             │  │
│  │ 0.74 med   KeyError   api/webhooks       │ └─────────────────────────┘  │
│  │            PR #481 · 5h ago      Review →│                              │
│  │ 0.52 low   ValueError services/export    │ ┌─────────────────────────┐  │
│  │            Draft PR #480 · 1d    Review →│ │  Project health   0.84  │  │
│  └──────────────────────────────────────────┘ │  ████████████████░░░░   │  │
│                                                │  ▸ Error rate     0.90  │  │
│  ┌──────────────────────────────────────────┐ │  ▸ Unresolved P0/1 0.75 │  │
│  │  Top repeat offenders                    │ │  ▸ Fix rate       0.88  │  │
│  ├──────────────────────────────────────────┤ │  ▸ Time to PR     0.82  │  │
│  │ 1,247×  TypeError    calculate_total  ↑  │ └─────────────────────────┘  │
│  │   892×  KeyError     handle_webhook   →  │                              │
│  │   441×  TimeoutError fetch_inventory  ↓  │                              │
│  └──────────────────────────────────────────┘                              │
└────────────────────────────────────────────────────────────────────────────┘
```

### Panel specifications

**KPI tiles** — value at `text-3xl/650/tabular-nums`, delta with directional arrow, 24-point sparkline in `--blue-400`. Clicking navigates to the filtered list behind the number. **"Time saved"** uses a stated assumption (2.1 engineer-hours per merged fix, configurable) with a tooltip explaining the calculation — an unexplained value-claim metric erodes trust rather than building it.

**Error volume chart** — Recharts area, `--blue-500` stroke, 12% opacity fill, `--chart-grid` gridlines, no vertical gridlines. Annotations for deploys, PRs, and incidents render as vertical markers. Hovering shows a tooltip with count, affected users, and top issue. The deploy-marker-to-spike correlation is frequently the fastest diagnostic in the entire product.

**Live pipeline panel** — subscribes to `WSS /v1/projects/{id}/stream`. Each row shows a mini stage-progress track (10 dots) that fills in real time. Running items pulse. Completed items show confidence and PR number. Max 5 rows.

**Needs your review** — the highest-value panel. Sorted by confidence descending, then age ascending. Each row is one click from the full investigation.

**Project health** — composite score from `GET /projects/{id}/health`, with the four components always expanded.

---

## 3. Issue list — `/issues`

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Issues                                           [ + Manual investigation]│
├────────────────────────────────────────────────────────────────────────────┤
│  🔍 Search…    Status ▾  Severity ▾  Env ▾  Service ▾  Last 7d ▾   Clear   │
│  ● open ×   ● P0,P1 ×   ● production ×                          34 issues  │
├────┬───────────────────────────────┬──────┬────────┬──────────┬────────────┤
│ SEV│ ERROR                         │COUNT │ TREND  │ LAST SEEN│ STATUS     │
├────┼───────────────────────────────┼──────┼────────┼──────────┼────────────┤
│ P1 │ TypeError                     │1,247 │▁▃▅▇▆▇▇▇│ 2m ago   │ 0.84 high  │
│    │ unsupported operand type(s)…  │      │  ↑ 2.4×│          │ PR #482 →  │
│    │ services/checkout.py::calcul… │      │        │          │            │
├────┼───────────────────────────────┼──────┼────────┼──────────┼────────────┤
│ P2 │ KeyError                      │  892 │▅▅▄▅▄▅▅▄│ 14m ago  │ 0.74 med   │
│    │ 'signature'                   │      │  → flat│          │ PR #481 →  │
│    │ api/webhooks.py::handle_stripe│      │        │          │            │
├────┼───────────────────────────────┼──────┼────────┼──────────┼────────────┤
│ P2 │ TimeoutError            ↻ REG │  441 │▇▅▃▂▁▁▂▃│ 1h ago   │ analyzing  │
│    │ inventory-service timeout      │      │  ↓ 0.6×│          │ ▸▸▸▸○○○○   │
│    │ services/inventory.py::fetch   │      │        │          │            │
└────┴───────────────────────────────┴──────┴────────┴──────────┴────────────┘
```

- Filters are URL state (`nuqs`) — every filtered view is a shareable link and the back button behaves correctly.
- Active filters render as removable chips.
- Trend sparkline is 24 hourly buckets with a directional multiplier.
- `↻ REG` marks regressions — an issue that was resolved and came back. High-signal, deliberately prominent.
- `j`/`k` navigate, `Enter` opens.
- Bulk selection enables mute, resolve, and severity change.

---

## 4. Issue detail — `/issues/{id}`

```
┌────────────────────────────────────────────────────────────────────────────┐
│  ← Issues                                    [Mute ▾] [Resolve] [Investigate]│
│                                                                            │
│  P1  TypeError                                          1,247 occurrences  │
│  unsupported operand type(s) for +: 'decimal.Decimal' and 'NoneType'                    │
│  services/checkout.py::calculate_total  ·  POST /api/v2/checkout            │
│                                                                            │
│  First seen 28 Jul 14:02 · Last seen 2m ago · 41.3/hr · 89 users affected   │
│  production · v2.14.1, v2.14.3 · checkout-api                              │
├────────────────────────────────────────────────────────────────────────────┤
│  Occurrences   Investigations   Sample event   Similar issues              │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│    60┤                                    ╭──╮                            │
│      │                                 ╭──╯  ╰───────                     │
│    30┤                            ╭────╯                                  │
│      │──────────────────────────╯                                         │
│     0└──┬────────┬────────┬────────┬────────┬────────┬────                │
│      28Jul     30Jul    01Aug    02Aug    03Aug    04Aug                  │
│                              ▲                                            │
│                        v2.14.3 deployed                                   │
│                                                                            │
│  ┌────────────────────────────────────────────────────────────────────┐   │
│  │  Investigations for this issue                                     │   │
│  ├────────────────────────────────────────────────────────────────────┤   │
│  │  inv_01J2K9  4 Aug 09:14  awaiting review  0.84 high   PR #482  →  │   │
│  │  inv_01J1F2  30 Jul 22:01 validation_failed  —         3 attempts→  │   │
│  └────────────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────────────┘
```

The release annotation on the chart is the key element. When the volume step-change aligns with a deploy marker, the diagnosis is often visible before any AI output is read.

---

## 5. Investigation detail — `/investigations/{id}`

The centrepiece of the product. Six tabs.

### 5.1 Header (persistent across tabs)

```
┌────────────────────────────────────────────────────────────────────────────┐
│  ← Investigations                                                          │
│                                                                            │
│  TypeError · services/checkout.py::calculate_total          0.84  high     │
│  inv_01J2K3M4N5 · started 4 Aug 09:14:23 · completed in 1m 54s             │
│                                                                            │
│  ● awaiting review    acme/checkout-api @ v2.14.3    PR #482 ↗             │
│                                                                            │
│  64,312 in / 6,108 out tokens · $0.318 · 8 stages · 0 repairs              │
├────────────────────────────────────────────────────────────────────────────┤
│  Pipeline │ Evidence │ Patch │ Sandbox │ Review │ Raw                      │
└────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Pipeline tab — the signature view

```
┌──────────────────────────────────────┬─────────────────────────────────────┐
│                                      │  Stage detail: Retrieve             │
│  ✓ Receive                    12ms   │                                     │
│  │  1 event accepted                 │  Duration    6.12s                  │
│  │                                   │  Tokens      0 in / 0 out           │
│  ✓ Fingerprint                 84ms  │  Cost        $0.002 (embeddings)    │
│  │  a3f8b2c1 · 1,247th occurrence    │                                     │
│  │                                   │  ── Strategies ──────────────────   │
│  ✓ Triage                     140ms  │  Frame direct     2 files   6,200t  │
│  │  P1 · score 0.72 · investigate    │  Call graph       4 files   5,800t  │
│  │                                   │  Vector semantic  3 files   3,100t  │
│  ✓ Understand                 2.81s  │  Git history      5 items   2,100t  │
│  │  null_undefined · 2 frames        │  Test discovery   1 file    1,212t  │
│  │  3 hypotheses generated           │                                     │
│  │                                   │  ── Retrieved files ─────────────   │
│  ✓ Retrieve                   6.12s ◄│  ▸ services/checkout.py     1.00    │
│  │  7 files · 18,412t · quality 0.86 │  ▸ clients/tax_client.py    0.79    │
│  │                                   │  ▸ api/routes/checkout.py   0.85    │
│  ✓ Reason                    24.12s  │  ▸ tests/test_checkout.py   0.55    │
│  │  Root cause · 7-step chain        │  … 3 more                           │
│  │  4 evidence items bound           │                                     │
│  │                                   │  ── Quality 0.86 ───────────────    │
│  ✓ Patch                     13.40s  │  ✓ failure point resolved           │
│  │  3 files · +36/−4 · test included │  ✓ entry point resolved             │
│  │                                   │  ✓ 4 callees, 1 caller              │
│  ✓ Validate                  41.23s  │  ✓ tests found                      │
│  │  9/9 gates · 47/47 tests          │  ✓ release correlation found        │
│  │                                   │  ⚠ 1 gap: get_regional_config       │
│  ✓ Critique                  11.84s  │                                     │
│  │  approve_with_notes · 2 findings  │  [ View full input/output JSON ]    │
│  │                                   │                                     │
│  ✓ Score                      180ms  │                                     │
│  │  0.836 · high                     │                                     │
│  │                                   │                                     │
│  ✓ Publish                    3.90s  │                                     │
│  │  PR #482 opened                   │                                     │
│  │                                   │                                     │
│  ○ Awaiting decision                 │                                     │
└──────────────────────────────────────┴─────────────────────────────────────┘
```

Behaviour:

- Live via WebSocket. The running stage pulses; the connector fills downward over 400ms as each completes.
- Clicking a stage opens its detail in the right panel; the selection is URL state.
- `aria-live="polite"` announces transitions for screen readers.
- If a repair loop ran, the Validate node expands into a sub-list of attempts, each independently inspectable.
- "View full input/output JSON" opens a syntax-highlighted viewer with a copy button.

This view **is** the "glass box" principle made visible. It is what distinguishes RootTrace AI from a black-box tool that emits a PR and asks for trust.

### 5.3 Evidence tab

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Root cause                                                                │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ TaxClient.get_rate() catches HTTPError and returns None on any       │  │
│  │ non-200 response, so a 503 from the tax service silently yields      │  │
│  │ None, which calculate_total() then adds to a Decimal.                │  │
│  │                                                                      │  │
│  │ Category: unhandled_error_path                                       │  │
│  │ Introduced by 8a3f1c2 · dana@acme.io · 25 Jul                        │  │
│  │ "refactor: extract tax lookup into TaxClient"                        │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  Reasoning chain                                                           │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ ① OBSERVE                                                            │  │
│  │   The exception is raised at services/checkout.py:142 where          │  │
│  │   base_price (Decimal) is added to tax_amount.                       │  │
│  │   📄 services/checkout.py:140-143                              ↗     │  │
│  │   ┌────────────────────────────────────────────────────────────┐     │  │
│  │   │ 140     base_price = cart.subtotal()                       │     │  │
│  │   │ 141                                                        │     │  │
│  │   │ 142 ▸   subtotal = base_price + tax_amount                 │     │  │
│  │   └────────────────────────────────────────────────────────────┘     │  │
│  │                                                                      │  │
│  │ ② OBSERVE   tax_amount assigned from get_rate() with no None check   │  │
│  │ ③ HYPOTHESISE  get_rate returns None under some condition   p=0.70   │  │
│  │ ④ TEST      ✓ Confirmed — except branch returns None                 │  │
│  │   📄 clients/tax_client.py:38-43                               ↗     │  │
│  │ ⑤ TEST      ✓ Breadcrumb: tax-service 503 at T−141ms                 │  │
│  │ ⑥ CHAIN     Commit 8a3f converted a raise into a return None         │  │
│  │ ⑦ CONCLUDE  Root cause is the broken error contract, not the outage  │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  Ruled out                                                                 │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ ✗ Missing regional tax configuration                                 │  │
│  │   The 503 breadcrumb and the except-branch return fully explain the   │  │
│  │   observation; no config lookup appears in the failing path.         │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
│  Context retrieved                                7 files · 18,412 tokens  │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  ▸ services/checkout.py       frame_direct     1.00   L100-190       │  │
│  │  ▸ clients/tax_client.py      vector_semantic  0.79   L1-68          │  │
│  │  ▸ api/routes/checkout.py     call_graph       0.85   L40-72         │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
```

Every claim carries a citation chip with an inline excerpt. Clicking `↗` opens the file at that line in a side panel. **The "Ruled out" section is deliberately prominent** — showing what was considered and rejected is what makes the conclusion credible rather than merely asserted.

### 5.4 Patch tab

Split view: file tree on the left with per-file `+/−` counts; Monaco diff on the right. Header shows base commit, total changes, and risk level. The AI's explanation sits above the diff. `alternatives_considered` renders as a collapsible "Considered and rejected" section beneath. Lines cited in the reasoning chain are highlighted in `--blue-50` with a gutter marker, so evidence and patch are visually linked.

### 5.5 Sandbox tab

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Validation · attempt 1 of 1                            ✓ passed  41.23s   │
│  Mode: full · roottrace/sandbox-python:3.12 · 412 MB peak · 1 CPU           │
├────────────────────────────────────────────────────────────────────────────┤
│  ✓ G0  Diff applies                                                 4ms    │
│  ✓ G1  Syntax                              3 files parsed          47ms    │
│  ✓ G2  Dependencies                        offline, 24 packages   4.82s    │
│  ✓ G3  Compile                                                    2.94s    │
│  ✓ G4  Regression test — pre-patch         FAILED as expected ✓   5.11s ◄  │
│  ✓ G5  Regression test — post-patch        PASSED                 4.80s    │
│  ✓ G6  Existing tests                      47 passed, 0 failed   14.20s    │
│  ✓ G7  Static analysis                     0 new high, 1 new med  7.40s    │
│  ✓ G8  Security scan                       clean                  1.90s    │
├────────────────────────────────────────────────────────────────────────────┤
│  Transcript                                    [copy] [wrap] [download]    │
│ ┌────────────────────────────────────────────────────────────────────────┐ │
│ │ 09:15:02.100  [G2] pip install --no-index --find-links=/opt/wheels     │ │
│ │ 09:15:06.920  [G2] Successfully installed 24 packages                  │ │
│ │ 09:15:12.030  [G4] running regression test on UNPATCHED code           │ │
│ │ 09:15:17.140  [G4] FAILED tests/test_checkout_tax.py::test_calculate…  │ │
│ │ 09:15:17.140  [G4]   TypeError: unsupported operand type(s)            │ │
│ │ 09:15:17.141  [G4] ✓ expected failure — test reproduces the bug        │ │
│ │ 09:15:22.250  [G5] applying patch and re-running                       │ │
│ │ 09:15:27.052  [G5] PASSED tests/test_checkout_tax.py::test_calculate…  │ │
│ └────────────────────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────────────────────┘
```

G4 is visually emphasised with a `◄` marker and an explanatory tooltip: *"The regression test must fail on the original code. If it passed, it wouldn't prove anything."* This is the gate users most need explained, and explaining it well is a large part of why they trust the result.

When repairs occurred, an attempt selector appears at the top and each attempt is fully inspectable — showing the failed attempts is a feature, not an embarrassment.

### 5.6 Review tab

Critic verdict badge, agreement score, findings grouped by severity with citations, security review, regression risk, test-quality assessment, and a note that the reviewer saw only the diff and the error — never the reasoning that produced them.

### 5.7 Raw tab

Everything, unabridged: full JSON for each stage, every LLM call with prompt/response links, token and cost breakdown per call, model and prompt version per stage, timing waterfall. For engineers who want to verify rather than trust.

---

## 6. Log explorer — `/logs`

```
┌────────────────────────────────────────────────────────────────────────────┐
│  Logs                                                    [Export ▾]        │
├────────────────────────────────────────────────────────────────────────────┤
│  🔍 error_type:TypeError AND service:checkout-api                          │
│  Last 24h ▾  Env ▾  Service ▾  Release ▾  Has investigation ▾  Valid ▾     │
│                                                8,421 events · 1.2 GB       │
├──────────────┬──────────┬──────────────────────────┬──────────┬───────────┤
│ RECEIVED     │ SERVICE  │ ERROR                    │ ROUTE    │ LINKS     │
├──────────────┼──────────┼──────────────────────────┼──────────┼───────────┤
│ 09:14:23.012 │ checkout │ TypeError                │ /checkout│ iss · inv │
│ 09:14:19.881 │ checkout │ TypeError                │ /checkout│ iss       │
│ 09:14:02.114 │ webhooks │ KeyError 'signature'     │ /stripe  │ iss · inv │
│ 09:13:58.220 │ checkout │ TypeError            🔒 2│ /checkout│ iss       │
└──────────────┴──────────┴──────────────────────────┴──────────┴───────────┘
```

- Virtualised list (TanStack Virtual) — smooth at 100k rows.
- Query syntax supports `field:value`, `AND`/`OR`/`NOT`, quoted phrases, and free text.
- `🔒 n` indicates redactions; hovering shows *what kind* was redacted (`secret_pattern`, `email`) but never the value.
- Clicking a row opens a detail drawer with the full sanitised payload in a collapsible JSON viewer, plus stack frames, breadcrumbs, request context, and links to the issue and investigation.
- Owner/maintainer can request the original raw blob; that action is written to `audit_log`.

---

## 7. Analytics — `/analytics`

### 7.1 Repeat errors — `/analytics/repeats`

The view that answers "what keeps costing us time."

```
┌────────────────────────────────────────────────────────────────────────────┐
│  87% of your error volume comes from 12% of distinct signatures            │
│  8,421 occurrences · 34 distinct issues · last 30 days                     │
├────────────────────────────────────────────────────────────────────────────┤
│  By volume                          By growth                              │
│  ┌─────────────────────────────┐   ┌─────────────────────────────┐         │
│  │ TypeError    ████████ 1,247 │   │ ValueError    ↑ 8.2×  312   │         │
│  │ KeyError     █████     892  │   │ TypeError     ↑ 2.4× 1,247  │         │
│  │ TimeoutError ███       441  │   │ KeyError      → 1.0×   892  │         │
│  └─────────────────────────────┘   └─────────────────────────────┘         │
│                                                                            │
│  Recurring after resolution                                          2     │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │ TimeoutError  resolved 12 Jul → regressed 30 Jul → 88 occurrences    │  │
│  │ ValueError    resolved 03 Jul → regressed 28 Jul → 41 occurrences    │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
```

### 7.2 Pipeline analytics — `/analytics/pipeline`

Stage success rates, p50/p95/p99 duration per stage, repair-loop frequency by failed gate, terminal-state distribution, retrieval quality distribution. This is where the team tunes the system.

### 7.3 Confidence calibration — `/analytics/confidence`

Predicted band vs. actual merge rate, plotted against the ideal diagonal. Honest self-measurement, shown to the user. A product that publishes its own calibration curve is making a strong statement about what kind of product it is.

### 7.4 Cost — `/analytics/cost`

Micro-USD by day, by stage, by model. Token breakdown. Cost per investigation trend. Quota usage against caps with a projection.

---

## 8. Settings

| Page | Contents |
|---|---|
| General | Name, description, severity threshold, investigated environments, cooldown, endpoint criticality map |
| Repositories | Connected repos, index status, **path-mapping tester** (`05` §6.6 — the fastest way to debug an integration), monorepo config |
| API keys | Create (reveal-once modal with copy + explicit warning), list with prefix and last-used, rotate, revoke |
| AI | Model tier overrides, confidence floor for PR, auto-merge policy (off by default, with a clear explanation of the risk), cost caps |
| Members | You (owner) — invite and role management ship in V2 |
| Audit | Filterable immutable action log |

The API key reveal modal is worth specifying precisely: full key in a monospace field, a prominent copy button, a plain-language warning that it will not be shown again, and a checkbox the user must tick to dismiss. Keys lost at creation are a common, entirely avoidable support burden.

---

## 9. Loading, empty, and error states

| State | Treatment |
|---|---|
| Initial load | Skeletons matching real content dimensions. Never a full-page spinner |
| List loading | Skeleton rows at exact row height, shimmer 1.5s |
| Chart loading | Grey placeholder at final chart height — layout must not shift |
| Live update | Content updates in place; brief `--blue-50` flash on the changed row |
| Empty (no data yet) | Illustration + explanation + primary action ("Send a test event") |
| Empty (filtered) | "No issues match these filters" + "Clear filters" |
| Error (fetch failed) | Inline card with the message, `request_id`, and a retry button |
| Error (boundary) | Friendly page, `request_id`, "Report this" mailto, "Back to overview" |
| Offline | Persistent banner; WebSocket auto-reconnects with backoff |
| Stale (WS dropped) | Amber "Reconnecting…" chip in the pipeline header; data marked as possibly stale |

`request_id` appears in every error surface. It is the single thing that turns "it broke" into a five-minute support resolution.

---

## 10. Performance targets

| Metric | Target |
|---|---|
| LCP (Overview) | < 1.2s |
| FID / INP | < 100ms |
| CLS | < 0.05 |
| TTI | < 2.0s |
| Initial JS bundle | < 180 KB gzipped |
| Route-level chunks | < 60 KB each |
| Log list scroll | 60fps at 100k rows |
| WS reconnect | < 2s |

Techniques: RSC for data-heavy pages, `next/dynamic` for Monaco and Recharts (both large, both below the fold), virtualisation for long lists, `next/font` with `display: swap`, TanStack Query with 30s stale time and background refetch, optimistic updates on mutations.

---

## 11. Frontend build checklist

- [ ] Design system tokens are the only source of colour
- [ ] Every page has loading, empty, and error states
- [ ] All lists are keyboard navigable (`j`/`k`/`Enter`)
- [ ] Command palette (`⌘K`) reaches every page and primary action
- [ ] Every AI claim renders with an evidence citation
- [ ] Pipeline viewer reconnects cleanly and re-syncs from snapshot
- [ ] All monetary and numeric values use `tabular-nums`
- [ ] `request_id` surfaced in every error state
- [ ] No `localStorage` for auth tokens
- [ ] Lighthouse ≥ 95 on performance and accessibility
- [ ] Playwright covers: login → overview → issue → investigation → all six tabs

---

*Next: [`10-DESIGN-SYSTEM.md`](./10-DESIGN-SYSTEM.md)*
