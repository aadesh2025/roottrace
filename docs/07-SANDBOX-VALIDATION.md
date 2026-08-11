# 07 — Sandbox Validation

> The in-app compiler. We execute AI-generated code to prove it works — inside a container that has no network, no credentials, no writable root, and no path back into the platform.

---

## 1. Why we build this, and why it is the riskiest component

### 1.1 The requirement

An unvalidated patch is a guess. The entire trust model of RootTrace AI rests on the claim *"this patch was compiled and tested before you were asked to look at it."* That claim requires executing code that a language model wrote, which is by definition untrusted input.

### 1.2 The alternative we deliberately rejected for V1

The prior architecture note recommended using the customer's own CI instead of building a sandbox — no execution infrastructure of our own, no security surface, and validation against the team's real pipeline. That reasoning is sound, and **we still adopt it in V2 as a second gate.** It is not sufficient as the V1 gate for four reasons:

| Reason | Detail |
|---|---|
| V1 runs on fake data | There is no customer repo and no CI to poll. A CI-only design cannot be built or tested at all in V1 |
| Requires an open PR first | GitHub CI only runs after we push a branch. That means publishing unvalidated code to a customer's repo to find out if it works — inverting our core promise |
| Not all repos have CI | Excluding repos without CI excludes a large share of exactly the users who need us most (solo founders, small teams) |
| No repair loop | CI feedback takes minutes and requires a new commit per attempt. Our loop needs seconds and must not litter the customer's repo with failed attempts |

**Decision: build the sandbox, run it before publishing, and add repo CI in V2 as an additional post-publish gate.** Recorded as `ADR-001`.

### 1.3 Honest risk statement

This is the highest-risk component in the system. We are executing untrusted code. The mitigation is defence in depth: **eight independent layers**, any one of which is individually sufficient to prevent the worst outcomes, and all eight of which must fail simultaneously for a serious breach.

---

## 2. Threat model

| # | Threat | Attack path | Primary mitigation |
|---|---|---|---|
| T1 | Container escape to host | Kernel exploit via syscall | gVisor / seccomp allowlist, non-root, dropped capabilities, no privileged mode |
| T2 | Network exfiltration | Patch opens a socket and sends source code out | **No network namespace with routes.** Physically cannot reach anything |
| T3 | Credential theft | Patch reads env vars or files for tokens | Zero secrets in the container. Env is scrubbed to a fixed allowlist |
| T4 | Cross-tenant data access | Patch reads another tenant's files | One container per validation. Nothing but this tenant's bundle is mounted. Destroyed after |
| T5 | Resource exhaustion / DoS | Fork bomb, infinite loop, memory balloon | Hard cgroup limits: CPU, memory, PIDs, disk, wall-clock kill |
| T6 | Persistence | Patch writes a backdoor for a later run | Read-only rootfs; the single writable path is a tmpfs destroyed on exit |
| T7 | Supply-chain injection | Patch adds a malicious dependency | Offline dependency resolution from a pre-vetted cache. Network install is impossible |
| T8 | Log-based injection | Malicious output crafted to attack our log pipeline or the UI | Output size caps, byte sanitisation, treated as untrusted data on render |
| T9 | Timing / side channel | Inferring host state | Low value given no network to exfiltrate through; accepted residual risk |
| T10 | Malicious test code | The "test" performs the attack rather than the patch | Tests run under identical restrictions. There is no privileged path |

---

## 3. The eight isolation layers

```
┌──────────────────────────────────────────────────────────────────────┐
│ L1  NETWORK        no interfaces except loopback; no routes; no DNS  │
├──────────────────────────────────────────────────────────────────────┤
│ L2  FILESYSTEM     read-only rootfs; /work is tmpfs; no host mounts   │
├──────────────────────────────────────────────────────────────────────┤
│ L3  IDENTITY       uid 65534 (nobody); all capabilities dropped       │
├──────────────────────────────────────────────────────────────────────┤
│ L4  SYSCALL        seccomp allowlist; gVisor runtime where available  │
├──────────────────────────────────────────────────────────────────────┤
│ L5  RESOURCES      cgroups: 1 CPU, 512 MB, 128 PIDs, 256 MB disk     │
├──────────────────────────────────────────────────────────────────────┤
│ L6  TIME           90 s hard SIGKILL by the supervisor, not the guest │
├──────────────────────────────────────────────────────────────────────┤
│ L7  SECRETS        empty environment except a fixed allowlist         │
├──────────────────────────────────────────────────────────────────────┤
│ L8  LIFECYCLE      one container per validation; destroyed on exit    │
└──────────────────────────────────────────────────────────────────────┘
```

### L1 — Network isolation

```yaml
network_mode: none          # no eth0 at all — not a firewall rule, an absence
dns: []
extra_hosts: []
```

This is the strongest single control. There is no interface to bind, no route to take, and no resolver to query. Even a complete compromise of the container yields nothing that can leave it. Dependency resolution is offline (§5), so nothing legitimate needs the network either.

### L2 — Filesystem

```yaml
read_only: true                      # entire rootfs immutable
tmpfs:
  /work:  { size: 256m, mode: 1777, noexec: false }   # the only writable path
  /tmp:   { size: 64m,  mode: 1777, noexec: true  }
volumes: []                          # zero host mounts. none.
```

The input bundle is **copied in via `docker cp` before start**, not bind-mounted. A bind mount is a live path to the host filesystem; a copy is not.

### L3 — Identity and capabilities

```yaml
user: "65534:65534"                  # nobody:nogroup
cap_drop: [ALL]
cap_add: []                          # nothing added back
security_opt:
  - no-new-privileges:true
  - apparmor=roottrace-sandbox
privileged: false
```

### L4 — Syscall filtering

Preferred runtime is **gVisor** (`runsc`), which intercepts syscalls in userspace and presents a reimplemented kernel surface — an entire class of kernel exploits stops working. Where gVisor is unavailable, a seccomp allowlist is applied:

Denied outright: `mount`, `umount2`, `pivot_root`, `chroot`, `ptrace`, `process_vm_readv/writev`, `bpf`, `perf_event_open`, `kexec_load`, `init_module`, `delete_module`, `reboot`, `setns`, `unshare`, `clone` with new-namespace flags, `keyctl`, `add_key`, `userfaultfd`, `io_uring_setup`.

### L5 — Resource limits

```yaml
cpus: "1.0"
mem_limit: 512m
memswap_limit: 512m        # equal to mem_limit → swap disabled
pids_limit: 128            # fork-bomb containment
ulimits:
  nofile: { soft: 256, hard: 512 }
  nproc:  { soft: 64,  hard: 128 }
  fsize:  { soft: 67108864, hard: 67108864 }   # 64 MB max single file
storage_opt: { size: 256m }
```

### L6 — Time

The 90 s limit is enforced by the **supervising worker**, not by anything inside the guest:

```python
try:
    result = await asyncio.wait_for(container.wait(), timeout=90.0)
except asyncio.TimeoutError:
    await container.kill(signal="SIGKILL")     # not SIGTERM — no cleanup hook to abuse
    return ValidationResult(passed=False, failed_gate="timeout", ...)
finally:
    await container.remove(force=True, v=True)
```

Per-gate soft timeouts (dependency 10 s, tests 20 s, static analysis 10 s) fail fast inside the overall budget.

> **90 s hard kill, 45 s p95 target (B11).** The original 45 s hard kill could not accommodate the gate sequence it was meant to bound. G6 runs the existing suite **twice** — a pre-patch baseline plus the post-patch run — because only *newly* failing tests count against a patch; G7 runs static analysis twice for the same reason. Single-pass soft budgets sum to 43 s, but the real worst case adds the second G6 (+15 s) and the second G7 (+8 s) ≈ **66 s**. A 45 s kill would have destroyed healthy validations mid-suite, recorded `build_passed: false`, and collapsed confidence to 0 via the S11 hard gate — presenting as poor patch quality rather than as the timeout it was. The kill is therefore 90 s. **45 s is retained as the p95 target** and is what `12` §3.5 alerts on. Per-gate soft budgets are unchanged.

### L7 — Environment scrubbing

```python
SANDBOX_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "HOME": "/work",
    "LANG": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
    "PIP_NO_INDEX": "1",
    "PIP_FIND_LINKS": "/opt/wheels",
    "NPM_CONFIG_OFFLINE": "true",
    "NPM_CONFIG_CACHE": "/opt/npm-cache",
    "CI": "true",
}
# Explicitly NOT inherited: everything else. The worker's environment —
# database URLs, LLM keys, GitHub tokens, Supabase service key — never enters.
```

A CI-runner integration test asserts that no variable matching `(KEY|TOKEN|SECRET|PASSWORD|DSN|URL)` exists inside a running sandbox.

### L8 — Lifecycle

One container per validation attempt. Never reused, never pooled with state. Removed with `force=True, v=True` (volumes deleted) in a `finally` block, plus a reaper job that destroys any container older than 120 s regardless of state.

---

## 4. Container images

One image per language, pre-built and pinned by digest. The image is built at CI time, scanned, and never modified at runtime.

```dockerfile
# apps/sandbox-runner/python/Dockerfile
FROM python:3.12-slim-bookworm@sha256:<pinned>

RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc libffi-dev \
 && rm -rf /var/lib/apt/lists/*

# Pre-warmed offline wheel cache — the top ~600 PyPI packages by download count,
# refreshed weekly by a scheduled job, scanned by pip-audit before publication.
COPY wheels/ /opt/wheels/

# Analysis toolchain, version-pinned so results are reproducible
RUN pip install --no-index --find-links=/opt/wheels \
      pytest==8.2.0 pytest-timeout==2.3.1 pytest-json-report==1.5.0 \
      ruff==0.5.0 mypy==1.10.0 bandit==1.7.9 coverage==7.5.0

COPY runner.py /opt/runner.py
RUN chmod 555 /opt/runner.py

# B10: the input bundle is staged here before start, outside the /work tmpfs.
RUN mkdir -p /opt/roottrace && chmod 755 /opt/roottrace

RUN useradd -u 65534 -r -s /usr/sbin/nologin sandbox || true
USER 65534:65534
WORKDIR /work

ENTRYPOINT ["python", "/opt/runner.py"]
```

| Image | Base | Toolchain | Phase |
|---|---|---|---|
| `roottrace/sandbox-python:3.12` | `python:3.12-slim` | pytest, ruff, mypy, bandit | **V1** |
| `roottrace/sandbox-node:20` | `node:20-slim` | vitest/jest, eslint, tsc | **V2** — the V1 fixture corpus is Python-only (`A1`), so a Node runner would have nothing to validate |
| `roottrace/sandbox-go:1.22` | `golang:1.22-alpine` | go test, go vet, staticcheck | **V5** (`16` §5) |

Images are rebuilt weekly (security patches), scanned with Trivy, and rolled out by digest. A digest change is a deliberate, reviewed deploy.

---

## 5. Offline dependency resolution

The container cannot reach a package registry. Dependencies resolve from a pre-warmed local cache.

```
1. Parse the repo manifest from the retrieved context
   (requirements.txt / pyproject.toml / package.json — all already fetched by S5)
2. For each requirement, look for a satisfying artefact in /opt/wheels (or /opt/npm-cache)
3. Install strictly offline:
      pip install --no-index --find-links=/opt/wheels -r requirements.txt
      npm ci --offline --cache /opt/npm-cache
4. Missing package → DEGRADED MODE (below), never a network call
```

### Degraded mode

If a required package isn't cached, we do not fail the whole validation. We downgrade what we can prove:

| Cache coverage | Mode | Gates run | Confidence impact |
|---|---|---|---|
| All deps available | **full** | G0–G8 | none |
| Missing non-test deps only | **partial** | G0–G3, G7, G8; tests skipped | `test_pass_ratio` = null, validation component capped at 0.55 |
| Missing core deps | **syntax_only** | G0, G1, G7 | validation component capped at 0.35, band capped at `low` |

Degraded mode is stated explicitly in the UI and in the PR description. We never let a reduced check masquerade as a full one.

The cache is refreshed weekly from PyPI/npm download rankings, and any package appearing in a customer manifest is queued for inclusion in the next refresh.

---

## 6. The gate sequence in detail

### G0 — Diff applies (in-process, ~5 ms, no container)

```python
patched_files = apply_unified_diff(bundle.files, patch.diff)   # unidiff, in memory
```
Fails if any hunk doesn't apply. Costs nothing and prevents a wasted container start.

### G1 — Syntax parse (in-process, ~50 ms, no container)

Tree-sitter parse of every changed file. Any `ERROR` node fails the gate.

**G0 and G1 run before the container is created.** Roughly 15% of first attempts fail here, and catching them for 55 ms instead of 8 seconds materially changes the repair loop's latency and cost.

### G2 — Dependency resolution (~5 s)

Offline install. On success, records the resolved dependency set for reproducibility.

### G3 — Compile / import check (~3 s)

| Language | Check |
|---|---|
| Python | `python -c "import <module>"` for each changed module, plus `python -m compileall -q` |
| TypeScript (V2) | `tsc --noEmit` |
| JavaScript (V2) | `node --check` per file |
| Go (V5) | `go build ./...` |

### G4 — Regression test, pre-patch (~5 s) — **the critical gate**

```
1. Write the ORIGINAL (unpatched) files into /work
2. Write ONLY the new regression test
3. Run it
4. REQUIRE: it fails, and fails with an error matching the reported exception family
```

If it passes, the test doesn't reproduce the bug and is worthless as evidence. Route to S9 repair with strategy `regenerate_test_only` — the fix may well be right; the test is the problem.

If it fails with an *unrelated* error (e.g. `ImportError` rather than the expected `TypeError`), that is also a failure: the test is broken, not demonstrative.

### G5 — Regression test, post-patch (~5 s)

Apply the patch, re-run the same test. Must pass. Failure here means the fix does not fix the bug — route to **S6**, not S7. The diagnosis was wrong.

### G6 — Existing test suite (~15 s)

Scoped for speed and signal:

```
1. Tests discovered by S5 as covering the implicated symbols  (always run)
2. Tests in the same directory as any changed file            (always run)
3. Full suite if it fits the time budget                      (best effort)
```

Failures are classified:

| Classification | Meaning | Consequence |
|---|---|---|
| `newly_failing` | Passed pre-patch, fails post-patch | **Gate fails.** A real regression |
| `already_failing` | Failed pre-patch too | Noted, not counted against us |
| `flaky` | Inconsistent across two runs | Re-run once; if still inconsistent, exclude and flag |

The pre-patch baseline run is what makes this honest. Without it, a repo with pre-existing failures would fail every validation forever.

### G7 — Static analysis (~8 s)

Run pre- and post-patch; **only new findings count.**

| Language | Tools |
|---|---|
| Python | `ruff check`, `mypy --strict` (if configured), `bandit -ll` |
| TS/JS | `eslint`, `tsc --noEmit` |

| Finding delta | Result |
|---|---|
| New HIGH | Gate fails |
| New MEDIUM | Gate passes, `-0.05` on validation component |
| New LOW | Recorded only |

### G8 — Security scan of the diff (~2 s)

Pattern scan of *added lines only*:

| Pattern | Severity |
|---|---|
| `eval`, `exec`, `Function()`, `pickle.loads`, `yaml.load` (unsafe loader) | HIGH |
| `subprocess` with `shell=True` | HIGH |
| String-concatenated SQL | HIGH |
| Disabled TLS verification (`verify=False`, `rejectUnauthorized: false`) | HIGH |
| Hardcoded credential shapes | HIGH |
| Broadened auth/permission checks | HIGH — always blocking |
| Removed input validation | MEDIUM |
| New outbound network call | MEDIUM — flagged for human attention |

Any HIGH finding fails the gate **and** caps final confidence at 0. We never publish a patch that introduces a security defect, however cleanly it fixes the original bug.

---

## 7. Input bundle and result contract

### Input (staged at `/opt/roottrace/input.json` before start — **not** under `/work`)

> **B10 — why the bundle cannot live under `/work`.** `/work` is a **tmpfs mounted at container start** (L2). `docker cp` runs against a *created but not started* container and writes into the container's filesystem layer. When the container then starts, the tmpfs is mounted over `/work` and **hides everything previously written there** — the runner would find an empty directory and fail with a missing-input error on every single validation. The bundle is therefore staged at `/opt/roottrace/input.json`, which is on the read-only rootfs and outside every mount point. `runner.py` reads it at startup and materialises the working tree into `/work` itself.
>
> This changes nothing about isolation: `/opt` is read-only to the guest, the bundle contains no credentials, and results still leave via `/work/_roottrace/result.json`, which the supervisor copies out before removal.

```
 host                          container (created)         container (started)
 bundle ──docker cp──►  /opt/roottrace/input.json   ──►  /opt/roottrace/input.json   [ro, visible]
                        /work/            (empty)   ──►  /work/  [tmpfs mounted]     ← would have hidden it
                                                          runner.py reads /opt →
                                                          writes tree into /work
```

```jsonc
{
  "validation_id": "val_01J2K...",
  "language": "python",
  "language_version": "3.12",
  "attempt": 1,
  "files_original": { "services/checkout.py": "…", "clients/tax_client.py": "…" },
  "files_patched":  { "services/checkout.py": "…", "clients/tax_client.py": "…" },
  "new_files": { "tests/test_checkout_tax.py": "…" },
  "manifest": { "path": "requirements.txt", "content": "fastapi==0.111.0\nhttpx==0.27.0\n…" },
  "regression_test": {
    "path": "tests/test_checkout_tax.py",
    "test_id": "tests/test_checkout_tax.py::test_calculate_total_raises_when_tax_unavailable",
    "expected_pre": "fail",
    "expected_post": "pass",
    "expected_error_family": "type_error"
  },
  "existing_tests": ["tests/test_checkout.py"],
  "gates": ["G2","G3","G4","G5","G6","G7","G8"],
  "budgets": { "total_s": 45, "deps_s": 10, "tests_s": 20, "static_s": 10 }
}
```

### Output (`/work/_roottrace/result.json`, read after exit)

```jsonc
{
  "validation_id": "val_01J2K...",
  "passed": true,
  "mode": "full",                       // full | partial | syntax_only
  "gates": [ /* per-gate results — see 03 §S8 */ ],
  "failed_gate": null,
  "resource_usage": { "wall_ms": 41226, "cpu_ms": 28940, "peak_memory_mb": 412,
                      "peak_pids": 14, "disk_written_mb": 18 },
  "transcript": { "stdout_bytes": 48211, "stderr_bytes": 1204, "truncated": false },
  "signals_for_scoring": {
    "build_passed": true,
    "regression_test_valid": true,
    "test_pass_ratio": 1.0,
    "new_static_findings_high": 0,
    "new_static_findings_medium": 1,
    "degraded_mode": false
  }
}
```

Output caps: 512 KB stdout, 128 KB stderr. Beyond that, truncate from the middle (keeping head and tail, which is where the signal is) and mark `truncated: true`. Bytes are sanitised — control characters stripped, ANSI escapes removed — before storage or render.

---

## 8. Orchestration

```python
async def run_validation(bundle: SandboxInput) -> ValidationResult:
    # Pre-container gates — cheap, fail fast
    if not (g0 := check_diff_applies(bundle)).passed:  return fail(g0)
    if not (g1 := check_syntax(bundle)).passed:        return fail(g1)

    container = None
    try:
        async with SANDBOX_SEMAPHORE:                  # global concurrency cap
            container = await docker.containers.create(
                image=IMAGES[bundle.language],
                network_mode="none",
                read_only=True,
                user="65534:65534",
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true", "apparmor=roottrace-sandbox"],
                runtime="runsc" if GVISOR_AVAILABLE else None,
                mem_limit="512m", memswap_limit="512m",
                nano_cpus=1_000_000_000,
                pids_limit=128,
                tmpfs={"/work": "size=256m,mode=1777", "/tmp": "size=64m,mode=1777,noexec"},
                environment=SANDBOX_ENV,
                labels={"roottrace.validation_id": bundle.validation_id,
                        "roottrace.created_at": now_iso()},
            )
            # B10: stage OUTSIDE /work — the tmpfs mount at start would hide it.
            await copy_into(container, "/opt/roottrace/input.json", bundle.json())
            await container.start()

            try:
                await asyncio.wait_for(container.wait(), timeout=90.0)
            except asyncio.TimeoutError:
                await container.kill(signal="SIGKILL")
                return timeout_result(bundle)

            result = await read_result(container, "/work/_roottrace/result.json")
            transcript = await container.log(stdout=True, stderr=True)
            await store_transcript(bundle.validation_id, sanitize(transcript))
            return result

    finally:
        if container:
            with suppress(Exception):
                await container.delete(force=True, v=True)
```

Global concurrency is capped by a semaphore sized to `floor(host_cpus × 0.75)`. Sandbox load is the one thing that can starve a host, so it is bounded explicitly rather than left to autoscaling.

A **reaper** runs every 60 s: any container labelled `roottrace.validation_id` older than 120 s is force-removed regardless of state. Orphans are impossible to fully prevent; they are trivially cleaned up.

---

## 9. The repair loop

```
        S7 patch
           │
           ▼
        S8 validate ──── pass ────► S10 critique
           │
          fail
           │
           ▼
   ┌───────────────────────────────────┐
   │  attempt < 3 ?                    │
   │      no  → validation_failed      │
   │      yes → route by failed gate:  │
   │        G1 → fix syntax (S7, fast) │
   │        G2 → remove unavailable dep│
   │        G3 → fix compile error     │
   │        G4 → REGENERATE TEST only  │
   │        G5 → RE-DIAGNOSE (→ S6)    │
   │        G6 → resolve regression    │
   │        G7 → remediate findings    │
   │        G8 → remove unsafe pattern │
   └───────────────────────────────────┘
```

Empirically observed distribution on the fixture corpus:

| Attempt | Cumulative pass rate |
|---|---|
| 1 | ~60% |
| 2 | ~78% |
| 3 | ~85% |
| ≥4 | marginal — the loop stops here deliberately |

Attempt 4 costs another ~$0.10 and 60 s for a ~2% gain. Stopping at 3 and reporting `validation_failed` honestly is the better product decision. **Every attempt is retained and viewable in the UI** — seeing that the AI tried three approaches and explaining why each failed is genuinely useful information for the engineer who picks it up.

---

## 10. Repo CI as a second gate (V2)

The sandbox is the pre-publish gate. Repo CI becomes an additional post-publish gate.

```
S8 sandbox (ours)  ─── pass ──►  S12 publish PR
                                      │
                                      ▼
                            S12b await GitHub Checks
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
         checks pass            checks fail              no CI configured
              │                       │                       │
     confidence × 1.10       convert to draft,        confidence unchanged;
     comment "CI green"      comment with the         UI prompts "connect CI
                             failure, re-enter        to unlock a second
                             repair loop              validation layer"
```

The two gates are complementary. Ours is fast, offline, and runs before anything reaches the customer's repo. Theirs is authoritative, uses the team's real environment and full suite, and is infrastructure they already trust. **Neither replaces the other.**

---

## 11. Operational limits and known gaps

Stated plainly rather than hidden:

| Limitation | Impact | Mitigation |
|---|---|---|
| No network means no integration tests | Tests hitting real services fail or are skipped | Detect and skip them; report `partial` mode honestly |
| No database in the container | DB-dependent tests can't run | V2: optional ephemeral sqlite/postgres sidecar on an isolated internal network |
| Dependency cache misses | Degraded mode | Weekly refresh + demand-driven additions |
| Monorepos with complex build tooling | May not resolve | Detect and fall back to `syntax_only`, state it clearly |
| Compiled languages with long builds | May exceed 90 s | Per-language budgets; Go/Java raise the cap when added in V5 |
| Tests requiring specific fixtures/env | Fail spuriously | Baseline pre-patch run classifies them as `already_failing`, so they don't count against us |
| gVisor unavailable on some hosts | Weaker syscall isolation | Seccomp allowlist + AppArmor + network-none still hold; gVisor is defence-in-depth, not the only defence |

---

## 12. Security verification checklist

Run in CI on every change to the sandbox image or orchestration code. Any failure blocks the deploy.

- [ ] Container cannot resolve DNS (`getaddrinfo` fails)
- [ ] Container cannot open a TCP socket to any address
- [ ] `env` inside the container contains no variable matching `(KEY|TOKEN|SECRET|PASSWORD|DSN|URL)`
- [ ] Writing to `/`, `/usr`, `/etc`, `/opt` fails with `EROFS`
- [ ] `/work` is tmpfs and does not persist across two runs
- [ ] Process runs as uid 65534, `id -u` ≠ 0
- [ ] `mount`, `ptrace`, `unshare` return `EPERM`
- [ ] Fork bomb is contained by `pids_limit` and the container dies without affecting the host
- [ ] A 512 MB allocation attempt is OOM-killed inside the container only
- [ ] An infinite loop is SIGKILLed at 90 s
- [ ] The input bundle staged at `/opt/roottrace/input.json` is readable by the runner **after** start (B10 regression guard — proves the `/work` tmpfs does not shadow it)
- [ ] `/work` is empty at container start and contains only runner-written content thereafter
- [ ] No host path is visible under `/proc/self/mountinfo` beyond the expected tmpfs entries
- [ ] Container is removed within 5 s of exit; reaper removes any orphan within 120 s
- [ ] Two concurrent validations cannot observe each other's `/work`
- [ ] Transcript output is truncated at the cap and control bytes are stripped
- [ ] `pip install` with an uncached package fails offline rather than reaching the network

---

*Next: [`08-GITHUB-INTEGRATION.md`](./08-GITHUB-INTEGRATION.md)*
