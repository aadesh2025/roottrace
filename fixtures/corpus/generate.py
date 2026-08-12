"""Generate the 25 error payloads from real tracebacks.

`A1` §9 step 4: *produce the error payload by actually triggering the bug —
never hand-write a stack trace. Hand-written traces are subtly unrealistic in
ways that make the pipeline look better than it is.*

So this runs each trigger, walks the captured `__traceback__`, and writes the
`POST /v1/events` body from what actually happened: real frames, real line
numbers, real local variables, real context lines read off disk. The only
invented parts are the ones a production deployment genuinely adds and a local
run cannot know — the container hostname, the request that was in flight, the
breadcrumbs the SDK would have buffered.

Run with:

    uv run python -m fixtures.corpus.generate

Regenerating is safe and idempotent: the payloads are derived from the code, so
a fixture refactor is picked up rather than silently invalidating the corpus.
Deterministic by construction — no timestamps from the clock, no random ids.
"""

from __future__ import annotations

import json
import re
import traceback
from pathlib import Path
from types import TracebackType
from typing import Any

from fixtures.triggers import Reproduction, reproduce_all
from fixtures.triggers.cases import CASE_IDS

CORPUS_DIR = Path(__file__).resolve().parent.parent / "error-corpus"
FIXTURE_REPO = Path(__file__).resolve().parent.parent / "synthetic-repo"

#: The deployment path prefix. Frames are rewritten from the local checkout to
#: where the code lives in the container, because that is what an SDK reports
#: and what S5's frame-path resolution has to undo.
DEPLOY_PREFIX = "/app/"

#: `A1` §7: three cases deliberately use non-standard prefixes, to exercise the
#: heuristic and suffix-matching branches of the resolution cascade rather than
#: only the trivial one.
NON_STANDARD_PREFIXES: dict[str, str] = {
    "external-01": "/usr/src/app/",
    "config-02": "/workspace/services/",
    "boundary-02": "C:\\build\\app\\",
}

#: Frozen so the corpus is byte-identical across runs. The wall clock would
#: make every regeneration a diff, and `S2` fingerprinting must be reproducible.
BASE_TIMESTAMP = "2026-08-04T09:14:22.481Z"

RELEASE = "v2.14.3"
SERVICE = "checkout-api"

#: Per-case request context, breadcrumbs and tags. This is the part a local
#: trigger cannot know, and it is where the decisive evidence lives for the
#: breadcrumb-critical cases: `A1` §6 and `18` §7 pin the tax-service 503 to
#: T-141 ms for `null-prop-01`, and `external-01` cannot be solved from the
#: stack trace at all.
CONTEXT: dict[str, dict[str, Any]] = {
    "null-prop-01": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 412},
        "breadcrumbs": [
            ("2026-08-04T09:14:22.101Z", "db", "SELECT * FROM carts WHERE id=? (12ms)", "info"),
            (
                "2026-08-04T09:14:22.340Z",
                "http",
                "GET tax-service/rate?region=eu-west \u2192 503",
                "warning",
            ),
        ],
        "tags": {"region": "eu-west-1", "tenant_tier": "enterprise"},
        "extra": {"cart_item_count": 3, "cart_subtotal": "49.99"},
    },
    "null-prop-02": {
        "request": {"method": "POST", "route": "/api/v2/cart/{cart_id}/items", "duration_ms": 38},
        "breadcrumbs": [
            ("2026-08-04T09:14:22.410Z", "db", "SELECT * FROM carts WHERE id=? (9ms)", "info"),
            ("2026-08-04T09:14:22.455Z", "navigation", "stale tab replayed add-item", "info"),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "free"},
        "extra": {"sku": "sku-not-in-cart"},
    },
    "null-prop-03": {
        "request": {"method": "GET", "route": "/api/v2/checkout/{order_id}", "duration_ms": 21},
        "breadcrumbs": [
            ("2026-08-04T09:14:22.460Z", "config", "loaded settings for env=staging", "info"),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "pro"},
        "extra": {"tax_service_url": None},
    },
    "null-prop-04": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 209},
        "breadcrumbs": [
            ("2026-08-04T09:14:22.300Z", "db", "SELECT * FROM users WHERE id=? (7ms)", "info"),
            (
                "2026-08-04T09:14:22.418Z",
                "http",
                "GET billing/profile?user=u_9f2b1c \u2192 200 (no discount_profile)",
                "info",
            ),
        ],
        "tags": {"region": "us-west-1", "tenant_tier": "free"},
        "extra": {"plan": "free"},
    },
    "type-mismatch-01": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 780},
        "breadcrumbs": [
            (
                "2026-08-04T09:14:22.180Z",
                "http",
                'POST payments/charges \u2192 200 amount="49.99"',
                "info",
            ),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "pro"},
        "extra": {"provider": "stripe"},
    },
    "type-mismatch-02": {
        "request": {"method": "GET", "route": "/api/v2/cart/{cart_id}/items", "duration_ms": 64},
        "breadcrumbs": [
            ("2026-08-04T09:14:22.400Z", "http", "GET inventory/items/sku-1 \u2192 200", "info"),
        ],
        "tags": {"region": "eu-west-1", "tenant_tier": "pro"},
        "extra": {"sku": "sku-1"},
    },
    "type-mismatch-03": {
        "request": {"method": "POST", "route": "/api/v2/pricing/import", "duration_ms": 1502},
        "breadcrumbs": [
            ("2026-08-04T09:14:21.900Z", "job", "supplier price book import started", "info"),
            ("2026-08-04T09:14:22.310Z", "job", "merged 1,204 supplier prices", "info"),
        ],
        "tags": {"region": "eu-west-1", "tenant_tier": "enterprise"},
        "extra": {"supplier": "acme-wholesale"},
    },
    "key-error-01": {
        "request": {"method": "POST", "route": "/api/v2/webhooks/stripe", "duration_ms": 12},
        "breadcrumbs": [
            (
                "2026-08-04T09:14:22.470Z",
                "http",
                "inbound webhook probe, headers=[content-type]",
                "info",
            ),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "pro"},
        "extra": {"provider": "stripe", "probe": True},
    },
    "key-error-02": {
        "request": {"method": "POST", "route": "/api/v2/cart/{cart_id}/coupon", "duration_ms": 18},
        "breadcrumbs": [
            ("2026-08-04T09:14:22.440Z", "ui", "coupon field submitted empty", "info"),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "free"},
        "extra": {},
    },
    "key-error-03": {
        "request": {"method": "GET", "route": "/api/v2/export/orders", "duration_ms": 3120},
        "breadcrumbs": [
            ("2026-08-04T09:14:19.200Z", "db", "SELECT * FROM orders LIMIT 5000 (2.4s)", "info"),
            ("2026-08-04T09:14:22.100Z", "job", "rendering CSV rows", "info"),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "enterprise"},
        "extra": {"guest_orders": 41},
    },
    "external-01": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 30412},
        "breadcrumbs": [
            ("2026-08-04T09:13:52.070Z", "db", "SELECT * FROM carts WHERE id=? (11ms)", "info"),
            (
                "2026-08-04T09:13:52.140Z",
                "http",
                "POST inventory/reserve sku=sku-1 \u2026 (30,006ms, no response)",
                "warning",
            ),
        ],
        "tags": {"region": "eu-west-1", "tenant_tier": "enterprise"},
        "extra": {"cart_item_count": 2},
    },
    "external-02": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 640},
        "breadcrumbs": [
            (
                "2026-08-04T09:14:22.300Z",
                "http",
                "POST payments/charges \u2192 429 Retry-After: 2",
                "warning",
            ),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "pro"},
        "extra": {"flash_sale": True},
    },
    "external-03": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 2044},
        "breadcrumbs": [
            (
                "2026-08-04T09:14:20.400Z",
                "http",
                "POST inventory/reserve \u2192 DNS failure (attempt 1)",
                "error",
            ),
            (
                "2026-08-04T09:14:21.440Z",
                "http",
                "POST inventory/reserve \u2192 DNS failure (attempt 2)",
                "error",
            ),
        ],
        "tags": {"region": "ap-south-1", "tenant_tier": "pro"},
        "extra": {"consecutive_failures": 5},
    },
    "race-01": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 96},
        "breadcrumbs": [
            (
                "2026-08-04T09:14:22.020Z",
                "job",
                "flash sale opened, 4 concurrent checkouts for sku-1",
                "info",
            ),
            ("2026-08-04T09:14:22.410Z", "db", "stock ledger read: sku-1 = 0", "warning"),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "pro"},
        "extra": {"oversold_by": 3},
    },
    "race-02": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 74},
        "breadcrumbs": [
            ("2026-08-04T09:14:22.300Z", "cache", "rate cache hit region=eu-west", "info"),
            (
                "2026-08-04T09:14:22.420Z",
                "deploy",
                "rate cache flushed by rolling restart",
                "warning",
            ),
        ],
        "tags": {"region": "eu-west-1", "tenant_tier": "enterprise"},
        "extra": {},
    },
    "boundary-01": {
        "request": {
            "method": "GET",
            "route": "/api/v2/cart/{cart_id}/items/first",
            "duration_ms": 15,
        },
        "breadcrumbs": [
            ("2026-08-04T09:14:22.450Z", "ui", "jump-to-item, page 2 of 2", "info"),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "free"},
        "extra": {"offset": 2},
    },
    "boundary-02": {
        "request": {"method": "GET", "route": "/api/v2/export/orders", "duration_ms": 880},
        "breadcrumbs": [
            ("2026-08-04T09:14:21.600Z", "job", "throttled overnight export, batch size 1", "info"),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "enterprise"},
        "extra": {"batch_size": 1},
    },
    "config-01": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 27},
        "breadcrumbs": [
            ("2026-08-04T09:14:22.455Z", "ui", "first checkout from region eu-north", "info"),
        ],
        "tags": {"region": "eu-north-1", "tenant_tier": "pro"},
        "extra": {"region": "eu-north"},
    },
    "config-02": {
        "request": {"method": "GET", "route": "/api/v2/export/orders", "duration_ms": 33},
        "breadcrumbs": [
            ("2026-08-04T09:14:22.440Z", "config", "EXPORT_BATCH_SIZE unset", "warning"),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "pro"},
        "extra": {"app_env": "staging"},
    },
    "regression-01": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 44},
        "breadcrumbs": [
            ("2026-08-04T09:14:22.300Z", "deploy", "v2.14.2 rolled out 6 days ago", "info"),
            ("2026-08-04T09:14:22.460Z", "db", "SELECT discount_profile (5ms)", "info"),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "pro"},
        "extra": {"release": "v2.14.3"},
    },
    "regression-02": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 512},
        "breadcrumbs": [
            (
                "2026-08-04T09:14:22.180Z",
                "http",
                "GET tax-service/rate?region=eu-west \u2192 503",
                "warning",
            ),
            ("2026-08-04T09:14:22.430Z", "job", "quote/charge reconciliation", "info"),
        ],
        "tags": {"region": "eu-west-1", "tenant_tier": "enterprise"},
        "extra": {"quoted": "49.99", "charging": "59.99"},
    },
    "regression-03": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 61},
        "breadcrumbs": [
            ("2026-08-04T09:14:22.300Z", "deploy", "v2.14.2 rolled out 6 days ago", "info"),
            ("2026-08-04T09:14:22.440Z", "ui", "cart page displayed 49.99", "info"),
        ],
        "tags": {"region": "eu-west-1", "tenant_tier": "pro"},
        "extra": {"displayed": "49.99", "charged": "59.99"},
    },
    "resource-01": {
        "request": {"method": "GET", "route": "/api/v2/export/orders", "duration_ms": 48200},
        "breadcrumbs": [
            ("2026-08-04T09:13:34.100Z", "job", "export started, 40,000 orders", "info"),
            ("2026-08-04T09:14:18.900Z", "runtime", "rss 1.9 GB, container limit 2 GB", "warning"),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "enterprise"},
        "extra": {"order_count": 40000},
    },
    "unfixable-01": {
        "request": {"method": "POST", "route": "/api/v2/checkout", "duration_ms": 118},
        "breadcrumbs": [
            (
                "2026-08-04T09:14:22.300Z",
                "http",
                "POST inventory/reserve \u2192 503 scheduled maintenance",
                "warning",
            ),
            (
                "2026-08-04T09:14:22.305Z",
                "status",
                "inventory-service maintenance window 09:00-10:00Z",
                "info",
            ),
        ],
        "tags": {"region": "us-east-1", "tenant_tier": "pro"},
        "extra": {"maintenance_window": True},
    },
    "unfixable-02": {
        "request": {"method": "GET", "route": "/health/ready", "duration_ms": 5002},
        "breadcrumbs": [
            (
                "2026-08-04T09:14:17.400Z",
                "infra",
                "cluster DNS resolver reported unhealthy",
                "error",
            ),
            (
                "2026-08-04T09:14:22.400Z",
                "http",
                "GET inventory/health \u2192 temporary failure in name resolution",
                "error",
            ),
        ],
        "tags": {"region": "eu-west-1", "tenant_tier": "enterprise"},
        "extra": {"resolver": "coredns"},
    },
}


def _deploy_path(case_id: str, absolute: Path) -> str:
    """Rewrite a local checkout path to where the code runs in production."""
    try:
        relative = absolute.relative_to(FIXTURE_REPO).as_posix()
    except ValueError:
        # A frame from the harness or the standard library. Reported as-is;
        # `in_app` will be false.
        return absolute.as_posix()

    prefix = NON_STANDARD_PREFIXES.get(case_id, DEPLOY_PREFIX)
    if prefix.endswith("\\"):
        return prefix + relative.replace("/", "\\")
    return prefix + relative


def _is_in_app(absolute: Path) -> bool:
    try:
        absolute.relative_to(FIXTURE_REPO)
    except ValueError:
        return False
    return True


def _source_lines(absolute: Path) -> list[str]:
    try:
        return absolute.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


#: `<services.cart.CartService object at 0x0000025F6EA233B0>` — the address
#: changes on every run.
_ADDRESS = re.compile(r" at 0x[0-9a-fA-F]+")


def _redact(value: object) -> str:
    """Render a local variable the way an SDK does: repr, then truncate.

    Real values, since the whole point of `vars` is that a diagnosis can see
    `tax_amount: None` without guessing. Ingest-time sanitisation (`03` §S1)
    redacts secrets separately, on the way in.

    Default object reprs carry a memory address, which is different on every
    run. Left in, the committed corpus would churn on every regeneration —
    every fixture a diff, and `make fixtures-verify` comparing against
    something nothing reproduces. The address is noise to a diagnosis anyway;
    the class name is the part that carries meaning.
    """
    text = _ADDRESS.sub("", repr(value))
    return text if len(text) <= 200 else text[:197] + "..."


def _walk(tb: TracebackType | None) -> list[TracebackType]:
    """Only the frames inside the synthetic repository.

    The trigger harness sits above the application on the stack, and a
    production SDK would never see it — the request enters through the
    framework, not through `_capture`. Dropping those frames is the same thing
    every SDK does with its own, and it also keeps the local checkout path out
    of a committed fixture.

    What remains is untouched: real frames, real line numbers, real locals.
    """
    kept: list[TracebackType] = []
    while tb is not None:
        if _is_in_app(Path(tb.tb_frame.f_code.co_filename).resolve()):
            kept.append(tb)
        tb = tb.tb_next
    if not kept:
        raise AssertionError("no application frames in the traceback")
    return kept


def _frames(case_id: str, tb: TracebackType | None) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for entry_tb in _walk(tb):
        frame = entry_tb.tb_frame
        absolute = Path(frame.f_code.co_filename).resolve()
        lineno = entry_tb.tb_lineno
        source = _source_lines(absolute)

        entry: dict[str, Any] = {
            "file": _deploy_path(case_id, absolute),
            "line": lineno,
            "function": frame.f_code.co_name,
            "in_app": True,
        }
        if source and 1 <= lineno <= len(source):
            entry["context_line"] = source[lineno - 1]
            entry["pre_context"] = source[max(0, lineno - 3) : lineno - 1]
            entry["post_context"] = source[lineno : lineno + 2]
        entry["vars"] = {
            name: _redact(value)
            for name, value in list(frame.f_locals.items())[:8]
            if not name.startswith("__") and name != "self"
        }
        frames.append(entry)

    # Innermost frame first, as every SDK reports it and as `S5`'s
    # `top_in_app_frames` expects.
    frames.reverse()
    return frames


def _stack_trace(case_id: str, exc: BaseException) -> str:
    """The formatted traceback for the application frames.

    Rendered by `traceback` from the real frame objects rather than assembled
    from strings, so the file, line, function and source line in every entry
    are the ones Python recorded. Harness frames are filtered for the reason
    in `_walk`; nothing inside a retained frame is edited except the path
    prefix, which is rewritten to where the code runs in the container.
    """
    summary = traceback.StackSummary.extract(
        ((entry.tb_frame, entry.tb_lineno) for entry in _walk(exc.__traceback__)),
        capture_locals=False,
    )
    body = "".join(traceback.format_list(summary))
    tail = "".join(traceback.format_exception_only(type(exc), exc))

    local_posix = FIXTURE_REPO.as_posix()
    local_native = str(FIXTURE_REPO)
    prefix = NON_STANDARD_PREFIXES.get(case_id, DEPLOY_PREFIX).rstrip("/\\")
    for local in (local_native, local_posix):
        body = body.replace(local, prefix)
    if not prefix.endswith("\\") and "\\" not in prefix:
        body = body.replace("\\", "/")
    return "Traceback (most recent call last):\n" + body + tail


def build_event(reproduction: Reproduction) -> dict[str, Any]:
    exc = reproduction.exception
    if exc is None:  # pragma: no cover — every trigger carries one
        raise AssertionError(f"{reproduction.case_id} produced no exception to report")

    case_id = reproduction.case_id
    context = CONTEXT[case_id]
    request = context["request"]

    return {
        "event_id": f"evt_fixture_{case_id.replace('-', '_')}",
        "timestamp": BASE_TIMESTAMP,
        "environment": "production",
        "service": SERVICE,
        "release": RELEASE,
        "level": "error",
        "error": {
            "type": type(exc).__name__,
            "message": str(exc),
            "stack_trace": _stack_trace(case_id, exc),
            "stack_frames": _frames(case_id, exc.__traceback__),
        },
        "request": {
            "method": request["method"],
            "url": request["route"],
            "route_pattern": request["route"],
            "status_code": 500,
            "duration_ms": request["duration_ms"],
            "headers": {"content-type": "application/json"},
        },
        "runtime": {
            "language": "python",
            "language_version": "3.12.4",
            "framework": "fastapi",
            "framework_version": "0.111.0",
            "os": "linux",
            "hostname": "checkout-api-7d9f-x4k2",
        },
        "user_context": {"user_hash": "u_9f2b1c", "plan": "pro", "is_authenticated": True},
        "breadcrumbs": [
            {"ts": ts, "category": category, "message": message, "level": level}
            for ts, category, message, level in context["breadcrumbs"]
        ],
        "tags": context["tags"],
        "extra": context["extra"],
    }


def main() -> None:
    CORPUS_DIR.mkdir(parents=True, exist_ok=True)
    reproductions = reproduce_all()

    missing = sorted(set(CASE_IDS) - set(CONTEXT))
    if missing:
        raise AssertionError(f"no request context for: {missing}")

    for case_id in CASE_IDS:
        payload = {"events": [build_event(reproductions[case_id])]}
        target = CORPUS_DIR / f"{case_id}.json"
        # newline="\n" explicitly: without it Python translates to CRLF on
        # Windows and LF on Linux, so the same generator would produce
        # different bytes on a developer machine and in CI, and every
        # regeneration would be a diff for whoever ran it on the other one.
        with target.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {target.relative_to(CORPUS_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
