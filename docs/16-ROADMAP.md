# 16 — Roadmap

> V2 through V6. Every phase ships independently and is useful on its own.

---

## 1. Phasing philosophy

Each phase must satisfy three tests:

1. **Independently shippable** — it works without the phases after it.
2. **Independently valuable** — a user would notice and care.
3. **Ordered by leverage** — the highest ratio of value to risk goes first.

We deliberately do **not** build the most exciting thing first. AI chat (V4) is the most demonstrable feature in this roadmap and it is fourth, because it is built entirely on top of investigations existing and being rich — which depends on everything before it.

---

## 2. V2 — Real repositories (weeks 11–18)

**Theme:** the pipeline proven in V1 now runs against real customer code.

| Feature | Value | Risk |
|---|---|---|
| GitHub App live mode | Unblocks everything | Medium — rate limits, path resolution on real repos |
| Repo CI as a second validation gate | Turns "we tested it" into "your CI tested it" | Low |
| Repository indexing + embeddings | Unlocks retrieval strategy C | Medium — cost and latency of initial index |
| Incremental re-index on merge | Makes indexing economically viable | Low |
| Sentry adapter | Removes the SDK-adoption barrier entirely | Low |
| Node/TypeScript SDK + sandbox runner | Roughly doubles the addressable market | Medium |
| Alert routing (Slack, email) | Closes the notification loop | Low |
| Noise suppression (mute, snooze, rate thresholds) | Prevents alert fatigue | Low |

### The path-resolution problem

This is V2's real risk and it deserves naming. Fixture repos have clean, predictable paths. Real repos have Docker prefixes, monorepo roots, symlinks, build-output directories, and bundled/minified frames. The `test_path_mapping` endpoint built in V1 exists precisely for this — it turns a frustrating trial-and-error integration into a ten-second diagnostic.

**Mitigation plan:** onboard the first ten repos manually, record every resolution failure, and turn each into a heuristic or a documented config option.

### CI as second gate

```
sandbox (ours) ── pass ──► PR opened ──► GitHub Checks polled
                                              │
              ┌───────────────────────────────┼──────────────────┐
              ▼                               ▼                  ▼
        checks pass                     checks fail        no CI configured
     confidence × 1.10              convert to draft,   confidence unchanged;
     comment "CI green"             comment, repair     prompt to connect CI
```

**V2 exit criteria:** 10 real repos connected · ≥ 70% of stack frames resolve to correct paths on first try · ≥ 30% PR merge rate · zero cross-tenant incidents · zero unauthorised repo writes.

---

## 3. V3 — Trust and autonomy (weeks 19–26)

**Theme:** the system earns the right to act with less supervision.

| Feature | Detail |
|---|---|
| **Feedback loop** | Merge/reject/edit outcomes recorded, analysed, and fed into the historical confidence component |
| **Learned retrieval weighting** | Strategy weights tuned per project from which retrieved files actually appeared in merged patches |
| **Confidence calibration** | Published reliability curve; the score becomes empirically meaningful rather than theoretically derived |
| **Auto-merge policy engine** | Opt-in per repo *and* per path glob, with a confidence floor, a daily cap, and instant revocation |
| **Multi-model consensus** | P0/P1 only; agreement raises confidence, disagreement surfaces both diagnoses side by side |
| **Team members and roles** | Owner / maintainer / viewer, invites, per-repo permissions |
| **Weekly digest email** | Trends, time saved, top offenders |
| **Saved views** | Persisted filters per user |

### Auto-merge: the trust ladder

Autonomy is granted incrementally and is always revocable.

```
Level 0   Every PR requires human approval                    ← V1/V2 default
Level 1   Auto-merge for test-only changes, confidence ≥ 0.95
Level 2   + non-critical paths (utils, formatters, logging)
Level 3   + any path except an explicit denylist
Level 4   + auto-merge on P0 during declared incidents
```

A project advances a level only after 20 consecutive correct outcomes at its current level. Any incorrect auto-merge drops it two levels immediately. This is deliberately conservative: one bad auto-merge destroys more trust than fifty good ones build.

### Multi-model disagreement is a feature

When models disagree, we show both diagnoses side by side with their evidence rather than picking one. An engineer reading "model A says the tax client, model B says the region resolver, here is each one's evidence" gets genuinely useful information. Hiding the disagreement behind an averaged score would throw that away.

**V3 exit criteria:** calibration error ≤ 0.10 per band · ≥ 40% merge rate · ≥ 5 projects on auto-merge level ≥ 1 · zero incorrect auto-merges.

---

## 4. V4 — Conversation (weeks 27–34)

**Theme:** investigations become interrogable.

| Feature | Detail |
|---|---|
| **AI chat per investigation** | Ask follow-up questions grounded in that run's artefacts |
| **Chat-driven patch refinement** | "Also handle the timeout case" → regenerate → re-validate → update the PR |
| **Cross-investigation search** | "Show me every issue caused by unhandled 5xx from a downstream service" |
| **Pattern detection** | "This is the fourth error caused by a client swallowing errors — here's the systemic fix" |
| **Codebase Q&A** | "What calls `get_rate`?" answered from the index |

### Why chat is fourth, not first

It is the most demonstrable feature in the roadmap and it would be the wrong thing to build early:

- It requires investigations to exist and be rich (V1–V2).
- It requires the reasoning artefacts to be trustworthy (V3 calibration).
- It requires the code index to answer codebase questions (V2 indexing).
- Built early, it would be an ungrounded chatbot over thin context — impressive in a demo, useless in practice, and actively damaging to trust the first time it confidently invents something.

### Design constraints (already reflected in the V1 schema)

- Scoped to one investigation by default. Small, cheap, grounded context.
- Every answer cites an artefact — the same evidence-binding rule as S6.
- Read-only in V4.0; patch refinement in V4.1 goes through the **full validation pipeline**, never a direct edit.
- `investigation_messages` already exists in the V1 schema, so this ships without a migration to existing tables.

**V4 exit criteria:** ≥ 30% of investigations receive at least one chat message · ≥ 90% of answers carry a valid citation · chat cost ≤ $0.05 per conversation.

---

## 5. V5 — Scale and languages (weeks 35–46)

**Theme:** more languages, more sources, more volume.

| Feature | Detail |
|---|---|
| Go, Java, Ruby support | Tree-sitter grammar + toolchain + sandbox runner per language |
| Datadog, OpenTelemetry, generic webhook adapters | Meet teams where their data already is |
| Frontend error support | Source-map resolution for minified JS stack traces |
| Performance issues, not just errors | N+1 queries, slow endpoints, memory growth |
| Read replicas + partition archival | Handle 100M+ events |
| Multi-region | Latency and data residency |
| Self-hosted option | Enterprise unblocker |

### Frontend errors deserve their own note

Minified stack traces are a genuinely different retrieval problem: the frame says `main.a4f2.js:1:48221`, and without source maps there is nothing to retrieve. It requires source-map ingestion at build time, a resolution step before S4, and bundler-specific handling. It is a substantial sub-project, not a small extension, which is why it sits in V5 rather than being bolted onto V2.

### Performance issues

The same pipeline shape with a different trigger and a different fix strategy:

```
Trigger      p95 latency regression, N+1 detection, memory growth
Understand   which endpoint, which query, which allocation
Retrieve     the ORM model, the query construction, the loop
Reason       why it's slow (missing index, N+1, unbounded fetch)
Patch        add eager loading / index migration / pagination
Validate     benchmark before and after in the sandbox
```

The validation gate becomes a benchmark rather than a test — "the patched version runs 40× fewer queries" is exactly the kind of provable claim this architecture is built to produce.

---

## 6. V6 — Platform (weeks 47+)

| Feature | Detail |
|---|---|
| Public API + webhooks | Customers build on top of RootTrace |
| **Billing** | Stripe, usage-based metering. The `usage_daily` and `llm_calls` ledgers have carried exact per-tenant cost since V1, so this is a surface, not an instrumentation project |
| CLI | `roottrace investigate <error-id>` locally |
| IDE extension | See investigations in-editor |
| Custom pipeline stages | Customer-defined validation gates |
| Custom models | Bring your own endpoint, including self-hosted |
| Fine-tuned models | Only once the feedback corpus is large enough to justify it |
| Marketplace | Community fingerprint rules, retrieval strategies, prompt packs |

Fine-tuning appears last for a reason: we have no proprietary dataset today. V3's feedback loop creates one. Attempting to fine-tune before there are thousands of merge/reject/edit outcomes would produce a model tuned on noise.

---

## 7. Explicitly not on the roadmap

| Not building | Why |
|---|---|
| **Dark mode** | A product decision, not a backlog item. One theme done perfectly |
| **Mobile app** | Nobody reviews a diff on a phone. Responsive web is sufficient |
| **Full-repo cloning** | Would undo our cost, latency, and security position simultaneously |
| **Generic "AI coding agent"** | Different product, different company. We are error-driven and validation-gated. That constraint is the product |
| **Replacing code review** | We augment reviewers with evidence. A product that positions itself as replacing review will not be adopted by the teams best equipped to evaluate it |
| **Deploying fixes automatically** | We open PRs. Deployment is the customer's pipeline and their decision |

---

## 8. Roadmap at a glance

| Phase | Weeks | Theme | Ships |
|---|---|---|---|
| **V1** | 1–10 | Prove the pipeline | 14 stages on fake data, full dashboard, sandbox |
| **V2** | 11–18 | Real repositories | Live GitHub, CI gate, indexing, Sentry, Node |
| **V3** | 19–26 | Trust and autonomy | Feedback loop, calibration, auto-merge, teams |
| **V4** | 27–34 | Conversation | AI chat, patch refinement, cross-investigation search |
| **V5** | 35–46 | Scale and languages | Go/Java/Ruby, frontend errors, performance issues |
| **V6** | 47+ | Platform | Public API, CLI, IDE, custom models, marketplace |

---

## 9. Guiding principle for every future decision

> **Does this make the system more trustworthy, or only more impressive?**

Trustworthy wins every time. A product that autonomously modifies production code earns adoption through accumulated evidence of correctness, and loses it permanently the first time it is confidently wrong in a way that reaches production. Every feature above is ordered by that logic: proof first, autonomy second, conversation third, breadth fourth.

---

*Next: [`17-GLOSSARY.md`](./17-GLOSSARY.md)*
