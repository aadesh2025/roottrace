# 08 — GitHub Integration

> How RootTrace AI reads exactly the code it needs — never the whole repository — and how it authors a branch, commit, and pull request without ever cloning anything.

---

## 1. Design constraints

| Constraint | Why | Consequence |
|---|---|---|
| **Never clone** | A clone means downloading, storing, and securing a customer's entire codebase. That is a liability, a cost, and a latency problem all at once | Selective fetch via the Contents and Git Data APIs |
| **Least privilege** | We should be unable to do things we don't need to do | GitHub App with a minimal, explicit permission set — never a personal access token |
| **Short-lived credentials** | A leaked long-lived token is catastrophic | Installation tokens minted per operation, ~60 min lifetime, never persisted |
| **Rate-limit resilient** | 5,000 requests/hour per installation is finite | Conditional requests with ETags, blob caching by SHA, request batching |
| **Auditable** | Every write to a customer repo must be attributable | Every API write logged to `audit_log` with actor, target, and investigation ID |
| **Revocable in one click** | Trust requires an exit | Uninstalling the App immediately and permanently ends all access |

---

## 2. GitHub App configuration

### 2.1 Permissions — the complete, minimal set

| Scope | Access | Why we need it |
|---|---|---|
| Contents | **Read & write** | Read source files; create blobs, trees, commits, and branches |
| Metadata | Read | Mandatory baseline |
| Pull requests | **Read & write** | Open PRs, read status, add labels and comments |
| Issues | Read & write | Label PRs (GitHub models PR labels as issue labels) |
| Checks | Read | V2 — read CI results |
| Commit statuses | Read | V2 — legacy status API |
| Actions | Read | V2 — read workflow run conclusions |
| Webhooks | Receive | `push`, `pull_request`, `check_suite`, `installation`, `installation_repositories` |

**Explicitly not requested:** Administration, Members, Organization secrets, Packages, Deployments, Environments, Security events, Codespaces. If the App does not need it, the App does not get it — and the permission screen a customer sees at install time is a security document we want to be short.

### 2.2 Install flow

```
1. User clicks "Connect GitHub" in the dashboard
2. Redirect → https://github.com/apps/roottrace-ai/installations/new?state=<signed_nonce>
3. User selects an org and either "all repositories" or a specific list
4. GitHub redirects back with ?installation_id=&setup_action=&state=
5. We verify the signed state nonce (CSRF), then:
   ├─ GET /app/installations/{id}          → account, permissions, repository_selection
   ├─ GET /installation/repositories        → the repo list
   ├─ INSERT github_installations
   └─ INSERT repositories (one row per selected repo, status = 'connected')
6. Webhook `installation.created` arrives independently and reconciles state
   (webhook and redirect can arrive in either order; both paths are idempotent)
```

### 2.3 Token minting

```python
def app_jwt() -> str:
    """Signed with the App private key. 10-minute life. Used ONLY to mint
    installation tokens — never to touch repository content."""
    now = int(time.time())
    return jwt.encode(
        {"iat": now - 60, "exp": now + 600, "iss": settings.github_app_id},
        settings.github_private_key,          # from KMS-backed secret store
        algorithm="RS256",
    )

async def installation_token(installation_id: int) -> str:
    """~60-minute installation token. Cached in Redis for 50 minutes.
    NEVER written to Postgres, NEVER logged, NEVER passed to a sandbox."""
    if tok := await redis.get(f"gh:tok:{installation_id}"):
        return decrypt(tok)
    resp = await http.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={"Authorization": f"Bearer {app_jwt()}"},
    )
    token = resp.json()["token"]
    await redis.setex(f"gh:tok:{installation_id}", 3000, encrypt(token))
    return token
```

The App private key lives in the secret store, is decrypted only in worker memory, and is used exclusively to sign the short-lived App JWT. Compromise of a cached installation token gives an attacker at most 50 minutes of scoped access to already-connected repos.

---

## 3. Selective retrieval — the core of the read path

### 3.1 Why selective, precisely

| Approach | Data transferred | Cost | Latency | Security surface |
|---|---|---|---|---|
| Full clone | 50 MB – 5 GB | High storage + egress | 10–120 s | Entire codebase at rest on our infra |
| Shallow clone | 5–500 MB | Medium | 3–30 s | Entire working tree at rest |
| **Selective fetch** | **20–200 KB** | **Negligible** | **0.5–3 s** | **Only implicated files, in memory** |

Selective fetch is 100–1000× less data, and the *only* files we ever hold are the handful the stack trace and call graph justify. This is simultaneously the cost strategy, the latency strategy, and the security story we tell customers.

### 3.2 Resolution: stack frame → repository path

The hard part. A stack frame says `/app/services/checkout.py`; the repository says `services/checkout.py`. Resolution runs a four-step cascade:

```
Step 1  Configured path mappings (per repository, highest confidence)
        [{ "from": "/app/",         "to": "" },
         { "from": "/usr/src/app/", "to": "" },
         { "from": "/workspace/",   "to": "services/api/" }]        → confidence 0.95

Step 2  Heuristic prefix stripping
        Strip any of: /app/, /usr/src/app/, /workspace/, /home/*/, /srv/,
        /var/task/ (Lambda), /var/www/, C:\...\                     → confidence 0.80

Step 3  Suffix matching against the cached repo tree
        Fetch the tree once (GET /git/trees/{sha}?recursive=1, cached by SHA),
        find paths whose suffix matches the frame path's tail.
        Unique match → 0.85.  Multiple → prefer the longest common suffix,
        then the shallowest path                                     → confidence 0.60

Step 4  Filename-only search
        Last resort. Unique basename match → 0.50. Ambiguous → 0.30 and
        flag low_frame_confidence in the UI
```

The repo tree is fetched **once per commit SHA** and cached in Redis for 24 h. A tree is a few hundred KB even for a large repo, it is immutable per SHA, and it makes resolution a local operation.

**Monorepo handling:** repositories carry an optional `root_path` (e.g. `services/checkout/`) and `service_map` binding the ingest `service` field to a subdirectory, so `service: "checkout-api"` scopes resolution to the right package before any matching happens.

#### Implementation note — the cascade spans two stages (added at T4.1/T4.2)

Steps 1–2 need no repository access and run in S4 (`03` §S4, `03` §8.1: *"S4 has no repo access by design"*) — `apps/worker/roottrace_worker/pipeline/understand/frames.py`. Steps 3–4 need the fetched tree and run in S5 — `apps/worker/roottrace_worker/pipeline/retrieve/path_resolution.py`. A step 1/2 result is **re-verified against the tree before being trusted**, including the configured-mapping case at 0.95: `config-02` in the corpus is a well-formed heuristic guess (`services/services/export.py`) that is not a real file, and only the tree can tell. Monorepo scoping is applied as a hard filter to steps 3–4 (and to a step 1/2 result being re-verified) — a match outside the scoped package is not returned even if it is unique in the whole tree.

`resolve_frame_path`/`resolve_against_tree` take an already-fetched `RepoTree`; the once-per-SHA Redis caching described above is the caller's responsibility (the orchestrator, T8.2), not this function's. Step 4's ambiguous case returns `resolved: null` rather than an arbitrary pick among the candidates — "flag `low_frame_confidence`" is implemented as an honest non-answer, not a guess dressed as one.

#### Implementation note — `search_symbol`'s contract is full-text, not definition-only (added at T4.3)

`§7.2`'s `search_symbol` returns every line where the queried symbol appears as a whole identifier, classified `kind="function"`/`"class"` for a definition and `kind="reference"` for every other occurrence — call sites, attribute access, imports, and comments or docstrings that happen to mention the name. This is deliberately textual, matching what a real GitHub code search actually returns; it is not AST-aware and does not attempt to tell a call site from a stray mention.

The reason: `03` §S5 strategy B's only V1 path to finding a function's *callers* is *"GitHub code search on the symbol name"* — `code_edges` is never populated — and a caller is a *use* of the function's name, never a second definition of it. A definition-only search, which is what `search_symbol` originally returned, could never find one. Precision belongs to the caller of this method, not to the transport: strategy B fetches a `"reference"` hit's file and confirms it with an `ast` parse before trusting it as a real call, exactly as a human skimming code search results would.

### 3.3 Fetching content

```python
async def fetch_file(repo, path: str, ref: str) -> FileContent:
    # 1. Cache by (repo_id, path, blob_sha) — blobs are immutable, so this
    #    cache never needs invalidation
    if blob_sha := await tree_cache.blob_sha(repo, path, ref):
        if cached := await blob_cache.get(repo.id, blob_sha):
            return cached

    # 2. Conditional request with ETag
    resp = await gh.get(
        f"/repos/{repo.full_name}/contents/{path}",
        params={"ref": ref},
        headers={"Accept": "application/vnd.github.raw",
                 **({"If-None-Match": etag} if (etag := await etag_cache.get(...)) else {})},
    )
    if resp.status_code == 304:            # does not count against rate limit
        return await blob_cache.get_by_etag(etag)

    # 3. Files > 1 MB require the Blobs API
    if resp.status_code == 403 and "too_large" in resp.text:
        return await fetch_via_blob_api(repo, blob_sha)

    content = resp.text
    await blob_cache.set(repo.id, blob_sha, content)
    return FileContent(path=path, content=content, sha=blob_sha, ref=ref)
```

**Which ref.** In priority order:

1. The commit SHA matching the error's `release`, if the project maps releases to SHAs. **Strongly preferred** — it means we read the code that actually ran.
2. The default branch at the error's timestamp (via the commits API, `until=<ts>`).
3. Default branch HEAD.

Reading the wrong revision is a subtle and expensive failure mode: the AI reasons about code that never executed. The retrieved `ref` is always recorded and displayed in the evidence panel so a reviewer can see exactly what was read.

### 3.4 Git history retrieval

```
Blame on the failing line range
  → GraphQL: repository.object(oid).blame(path).ranges
  → filter to the failing lines → introducing commit, author, date, message

Recent commits touching implicated paths
  → GET /repos/{o}/{r}/commits?path=<p>&per_page=10

Release correlation (high value, cheap)
  → GET /repos/{o}/{r}/compare/{prev_release}...{error_release}
  → filter files to the implicated set
  → if the failing function appears in that diff, it is very likely the cause

Open/recent PRs touching implicated paths
  → GET /repos/{o}/{r}/pulls?state=all&sort=updated&per_page=20
  → filter by changed files
```

Release correlation deserves emphasis: when an error first appears at `v2.14.3` and the failing function was modified in `v2.14.2..v2.14.3`, that is close to conclusive, costs one API call, and is often stronger evidence than anything the model can infer from the code alone.

### 3.5 Rate-limit management

| Technique | Effect |
|---|---|
| ETag conditional requests | 304s don't count against the 5,000/hr limit |
| Blob cache keyed by SHA | Immutable — a file fetched once is never fetched again at that SHA |
| Tree cache keyed by SHA | One call resolves every path in a repo |
| Parallel fetch with bounded concurrency | 8 concurrent, per installation |
| GraphQL for blame/PR queries | One round trip instead of N |
| Per-installation token bucket | Pre-emptive throttle at 80% of limit |
| `Retry-After` respect | On secondary rate limits, back off exactly as instructed |

Typical investigation cost: **12–25 API calls.** At 5,000/hour that supports ~200 investigations/hour per installation, far above any realistic rate.

---

## 4. Writing — branch, commit, PR without a clone

The Git Data API lets us construct a commit from raw objects. No working directory, no filesystem, no `git` binary.

```python
async def publish_patch(inv: Investigation, patch: Patch) -> PullRequestRecord:
    gh   = GitHubClient(await installation_token(inv.repo.installation_id))
    repo = inv.repo.full_name

    # 1. Base — the SHA the patch was generated against, for correctness
    base_sha = patch.base_commit

    # 2. Blob per changed file
    blobs = {}
    for f in patch.files_changed:
        r = await gh.post(f"/repos/{repo}/git/blobs",
                          json={"content": f.new_content, "encoding": "utf-8"})
        blobs[f.repo_path] = r.json()["sha"]

    # 3. Tree layered on the base tree — unchanged files are inherited, not re-uploaded
    tree = await gh.post(f"/repos/{repo}/git/trees", json={
        "base_tree": base_sha,
        "tree": [{"path": p, "mode": "100644", "type": "blob", "sha": s}
                 for p, s in blobs.items()],
    })

    # 4. Commit
    commit = await gh.post(f"/repos/{repo}/git/commits", json={
        "message": commit_message(inv, patch),
        "tree": tree.json()["sha"],
        "parents": [base_sha],
        "author":    {"name": "RootTrace AI", "email": "bot@roottrace.ai",
                      "date": now_iso()},
        "committer": {"name": "RootTrace AI", "email": "bot@roottrace.ai",
                      "date": now_iso()},
    })

    # 5. Branch
    branch = f"roottrace/fix-{inv.issue.fingerprint[:8]}"
    try:
        await gh.post(f"/repos/{repo}/git/refs",
                      json={"ref": f"refs/heads/{branch}", "sha": commit.json()["sha"]})
    except AlreadyExists:
        branch = f"{branch}-{inv.id.hex[:4]}"     # a prior attempt exists
        await gh.post(f"/repos/{repo}/git/refs",
                      json={"ref": f"refs/heads/{branch}", "sha": commit.json()["sha"]})

    # 6. Pull request — draft when confidence band is low
    pr = await gh.post(f"/repos/{repo}/pulls", json={
        "title": pr_title(inv),
        "head": branch,
        "base": inv.repo.default_branch,
        "body": render_pr_description(inv, patch),
        "draft": inv.confidence_band == "low",
        "maintainer_can_modify": True,
    })

    # 7. Labels
    await gh.post(f"/repos/{repo}/issues/{pr.json()['number']}/labels",
                  json={"labels": ["roottrace",
                                   f"confidence:{inv.confidence_band}",
                                   f"severity:{inv.issue.severity}"]})

    await audit_log.record(action="github.pr.created", target=f"{repo}#{pr.json()['number']}",
                           investigation_id=inv.id)
    return await store_pr_record(inv, pr.json())
```

### Commit message format

```
fix: handle tax service unavailability in checkout total

TaxClient.get_rate() returned None on any non-200 response, causing a
TypeError when calculate_total() added it to a Decimal. Restores error
propagation with a typed exception and adds a regression test.

Root cause introduced by 8a3f1c2 (refactor: extract tax lookup into TaxClient).

Fixes error signature a3f8b2c1 (1,247 occurrences since 2026-07-28).
Validated: build ✓ · regression test ✓ · 47/47 existing tests ✓ · static ✓
Confidence: 0.84 (high)

RootTrace-Investigation: inv_01J2K3M4N5P6Q7R8S9T0
RootTrace-Issue: iss_01J2K3M4N5P6Q7R8S9T0
```

Trailers are machine-readable, which makes correlating a merged commit back to its investigation trivial for the feedback loop.

---

## 5. Webhooks

### 5.1 Signature verification — always, first, constant-time

```python
@router.post("/v1/webhooks/github")
async def github_webhook(request: Request):
    body = await request.body()
    sig  = request.headers.get("X-Hub-Signature-256", "")
    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):        # constant-time
        raise HTTPException(401, "RT-AUTH-0020")

    delivery_id = request.headers["X-GitHub-Delivery"]
    if not await redis.set(f"gh:wh:{delivery_id}", "1", nx=True, ex=86400):
        return {"status": "duplicate"}                 # GitHub retries; be idempotent

    await enqueue("rt:github", handle_webhook,
                  event=request.headers["X-GitHub-Event"], payload=json.loads(body))
    return {"status": "accepted"}                      # must respond < 10 s
```

The handler enqueues and returns. GitHub disables webhooks that consistently time out, so no processing happens in the request path.

### 5.2 Events handled

| Event | Action |
|---|---|
| `installation.created` | Create installation + repository rows; kick off optional indexing |
| `installation.deleted` | **Immediately revoke all access**, mark repos disconnected, purge cached tokens and blobs |
| `installation_repositories.added/removed` | Sync the repository list |
| `pull_request.closed` (merged=true) | S13: `merged`. Compare merge head to our commit → `merged_unchanged` or `edited_and_merged` |
| `pull_request.closed` (merged=false) | S13: `rejected` |
| `pull_request_review.submitted` | Record reviewer verdict and comments as feedback signal |
| `pull_request.synchronize` | Human pushed to our branch → capture `human_edit_diff` |
| `push` to default branch | V2: enqueue incremental re-index of changed files |
| `check_suite.completed` | V2: read CI result for the PR, adjust confidence |

### 5.3 Detecting human edits — the highest-value signal we collect

```python
async def analyze_merge(pr_record, payload):
    our_sha   = pr_record.commit_sha
    merge_sha = payload["pull_request"]["merge_commit_sha"]

    commits = await gh.get(f"/repos/{repo}/pulls/{num}/commits")
    ours    = [c for c in commits if c["author"]["login"] == "roottrace-ai[bot]"]
    theirs  = [c for c in commits if c not in ours]

    if not theirs:
        return Feedback(outcome="merged_unchanged", signal_strength=1.0)

    diff = await gh.get(f"/repos/{repo}/compare/{our_sha}...{merge_sha}",
                        headers={"Accept": "application/vnd.github.diff"})
    return Feedback(
        outcome="edited_and_merged",
        human_edit_diff=diff.text,
        edit_analysis=await analyze_edit_semantics(pr_record, diff.text),  # fast-tier LLM
        signal_strength=0.85,
    )
```

`merged_unchanged` says we were right. `edited_and_merged` says we were *nearly* right and tells us exactly what we missed — which is far more actionable. Both are logged; the edit diff is what trains retrieval weighting in V3 (`16` §3).

---

## 6. Repository indexing (V2)

V1 retrieves entirely on demand. V2 adds a pre-built index so call-graph traversal and vector search work without live parsing.

```
Trigger: installation.created, or push to default branch

1. Determine changed files
   ├─ first index → GET /git/trees/{sha}?recursive=1 (all supported files)
   └─ incremental → GET /compare/{last_indexed}...{head} → changed files only

2. Per file, bounded concurrency:
   ├─ fetch content
   ├─ Tree-sitter parse → AST
   ├─ extract function/class nodes (name, signature, docstring, line range, source)
   ├─ extract edges (calls, imports, extends, implements)
   ├─ embed each node with a code embedding model
   └─ upsert code_nodes + code_edges

3. Delete nodes for removed files
4. UPDATE repositories SET last_indexed_sha, last_indexed_at, index_status='ready'
```

**Incremental is the whole point.** A 2,000-file repo takes ~8 minutes and ~$1.20 to index initially. A typical merge changes 3 files — ~4 seconds and ~$0.002. Re-indexing everything on every push would make the product economically unviable.

| Repo size | Nodes | Initial index | Embedding cost | Incremental |
|---|---|---|---|---|
| 200 files | ~1,800 | ~50 s | ~$0.12 | ~2 s |
| 2,000 files | ~18,000 | ~8 min | ~$1.20 | ~4 s |
| 20,000 files | ~180,000 | ~75 min | ~$12 | ~6 s |

---

## 7. The transport abstraction — fixture/live parity

V1 ships with the GitHub client fully implemented and a `GITHUB_MODE` switch. **The pipeline must be transport-independent**: no pipeline stage may contain a fixture-mode branch.

### 7.1 The layering

```
        S5 retrieve · S12 publish · S13 await_decision
                          │
                          │  depends ONLY on this interface
                          ▼
             ┌────────────────────────────┐
             │   GitHubGateway (Protocol) │   ← the seam
             └────────────┬───────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
 FixtureTransport   ReplayTransport    LiveTransport
 fixtures/          cassettes/         api.github.com
 synthetic-repo/    (VCR)              (App auth)
```

The **only** place `RT_GITHUB_MODE` is read is the gateway factory. A grep for `github_mode` outside `roottrace_worker/github/factory.py` is a build failure — this is enforced by a lint rule, because parity that depends on discipline will not survive ten weeks.

### 7.2 The interface

```python
class GitHubGateway(Protocol):
    # ── Read ────────────────────────────────────────────────────────────
    async def fetch_tree(self, repo: RepoRef, ref: str) -> RepoTree: ...
    async def fetch_file(self, repo: RepoRef, path: str, ref: str) -> FileContent: ...
    async def fetch_files(self, repo: RepoRef, paths: Sequence[str],
                          ref: str) -> list[FileContent]: ...
    async def blame(self, repo: RepoRef, path: str, ref: str,
                    line_range: tuple[int, int]) -> list[BlameRange]: ...
    async def recent_commits(self, repo: RepoRef, path: str,
                             limit: int = 10) -> list[Commit]: ...
    async def compare(self, repo: RepoRef, base: str, head: str) -> CompareResult: ...
    async def search_symbol(self, repo: RepoRef, symbol: str) -> list[SymbolHit]: ...

    # ── Write ───────────────────────────────────────────────────────────
    async def create_blob(self, repo: RepoRef, content: str) -> Sha: ...
    async def create_tree(self, repo: RepoRef, base_tree: Sha,
                          entries: Sequence[TreeEntry]) -> Sha: ...
    async def create_commit(self, repo: RepoRef, message: str, tree: Sha,
                            parents: Sequence[Sha], author: Actor) -> Sha: ...
    async def create_ref(self, repo: RepoRef, ref: str, sha: Sha) -> None: ...
    async def create_pull_request(self, repo: RepoRef, pr: PullRequestDraft
                                  ) -> PullRequestRef: ...
    async def add_labels(self, repo: RepoRef, number: int,
                         labels: Sequence[str]) -> None: ...
```

Every method returns a **domain type**, never a raw GitHub JSON payload. That is what keeps GitHub's response shape from leaking into the pipeline: `FixtureTransport` does not have to imitate GitHub's wire format, only to satisfy the same contract.

### 7.3 Modes

| Mode | Reads | Writes | Tier | Used by |
|---|---|---|---|---|
| `fixture` | `fixtures/synthetic-repo/` + `.roottrace-fixture.json` | `pull_request_records` with `is_simulated=true` | `evaluation` | **V1 default**, unit tests, eval harness |
| `replay` | Recorded VCR cassettes from a real repo | Asserted against recorded requests | `evaluation` | CI — exercises real response shapes with no live dependency |
| `live` | `api.github.com` under App auth | Real branches, commits, PRs | `live` only | V2+ |

In `fixture` mode **no network call to GitHub occurs at all**, and the boot invariant (`A3` §6) guarantees an `evaluation`-tier deployment holds no App private key, so it could not authenticate even if a call were attempted.

### 7.4 Contract tests — the parity guarantee

One test suite, parameterised over all three transports. **A transport is not complete until it passes every case.**

```python
@pytest.fixture(params=["fixture", "replay", "live"])
def gateway(request) -> GitHubGateway: ...

# every test below runs three times, once per transport
```

| # | Contract | Asserts |
|---|---|---|
| GC1 | `fetch_file` at a known ref | Byte-identical content and `sha` across transports |
| GC2 | `fetch_file` on a missing path | Raises `FileNotFound`, never returns empty content |
| GC3 | `fetch_tree` | Same path set; entries sorted identically |
| GC4 | `fetch_files` (batch) | Same result as N× `fetch_file`, order preserved |
| GC5 | `blame` on a known range | Same introducing SHA, author, and date |
| GC6 | `compare` across two releases | Same changed-file set and hunk boundaries |
| GC7 | `create_blob`/`tree`/`commit`/`ref` | Same call sequence and argument shapes |
| GC8 | `create_pull_request` | Identical rendered title and body |
| GC9 | Branch-name collision | Both raise `AlreadyExists`; both retry with the same suffix rule |
| GC10 | Rate limit / 403 | Both surface `RateLimited` with a `retry_after` |
| GC11 | Empty diff on PR creation | Both raise `NoDiff` → terminal `failed` with `RT-GITHUB-0006` |
| GC12 | Ref resolution priority | Release SHA → timestamped default branch → HEAD, identically |

**GC1 and GC8 are the load-bearing pair.** If fixture and live return the same bytes for the same file, and render the same PR body from the same investigation, then every stage between them is transport-blind by construction.

### 7.5 The V2 flip

Promoting to live GitHub is:

1. `RT_DEPLOYMENT_TIER=live`
2. `RT_GITHUB_MODE=live`
3. Provide `RT_GITHUB_APP_ID`, `RT_GITHUB_PRIVATE_KEY`, `RT_GITHUB_WEBHOOK_SECRET`
4. Set `repositories.github_live_enabled = true` per repo

**Zero application code changes.** If V2 requires touching a pipeline stage, the abstraction failed and that is a defect in V1, not new V2 work. The rendered PR description is stored and displayed in the dashboard in fixture mode exactly as it would appear on GitHub, so the V1 output is reviewable as the real artefact.

---

## 8. Failure handling

| Failure | Response |
|---|---|
| Installation token 401 | Re-mint once; if it fails again, mark the installation `needs_reauth` and surface it in the UI |
| 403 rate limit (primary) | Back off until `X-RateLimit-Reset`; queue the job |
| 403 secondary rate limit | Respect `Retry-After` exactly; exponential backoff |
| 404 on a file | Path resolution was wrong. Drop from the bundle, lower retrieval quality score, continue |
| 409 on branch creation | Suffix the branch name and retry once |
| 422 on PR creation | Usually "no commits between branches" — the patch was a no-op. Terminal `failed` with a clear reason |
| Repo archived / read-only | Analysis proceeds; publish is skipped with a plain explanation |
| Branch protection blocks the push | Publish fails gracefully; surface the protection rule so the user can allowlist our App |
| Webhook delivery missed | Reconciliation job polls open PRs older than 1 h to catch missed terminal events |

---

*Next: [`09-FRONTEND-DASHBOARD.md`](./09-FRONTEND-DASHBOARD.md)*
