"""`POST /v1/events` (`03` §S1, `05` §5).

The algorithm in `03` §S1, in order, with the two properties that matter most:

**The idempotency claim is atomic** (B7). There is no read-then-write anywhere
on this path.

**Partial success is real.** One malformed event never discards the rest of the
batch. A client sending from inside a crash handler cannot fix a rejection, so
losing 99 good events to one bad one loses exactly the errors it most needed to
report.

Sanitisation (step 6) is **T2.2**. The seam is here and every accepted payload
passes through it; the pattern corpus — AWS keys, GitHub tokens, JWTs, entropy,
Luhn — is that ticket's subject. `sanitise` currently returns the payload
unchanged and says so, rather than a partial implementation that would look
like coverage.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response

from roottrace_api.context import current_request_id
from roottrace_api.errors import ApiError, error_response
from roottrace_api.ingest import idempotency, keys, ratelimit, repository
from roottrace_api.ingest.events import MAX_BATCH_SIZE, ValidatedEvent, validate_batch
from roottrace_api.ingest.sanitise import sanitise
from roottrace_api.log import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/v1", tags=["ingest"])

#: `05` §1: 5 MB on ingest.
MAX_BODY_BYTES = 5 * 1024 * 1024

#: `03` §S1 step 9. The worker consumes this (ARQ, W2); the contract between
#: them is the queue name and the payload shape, not a shared library.
INGEST_QUEUE = "rt:ingest"


@router.post("/events", status_code=202)
async def receive_events(
    request: Request,
    response: Response,
    authorization: Annotated[str | None, Header()] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    started = time.perf_counter()

    # ── 1. Authenticate ────────────────────────────────────────────────
    try:
        plaintext = keys.extract_bearer(authorization)
    except keys.InvalidKeyError as exc:
        raise ApiError("RT-AUTH-0001", "Invalid or missing API key") from exc

    key_hash = keys.hash_key(plaintext)
    redis = request.app.state.redis
    pool = request.app.state.db_pool

    resolved = await _resolve(redis, pool, key_hash)
    if resolved is None:
        raise ApiError("RT-AUTH-0001", "Invalid or missing API key")
    if not resolved.may_write_events:
        raise ApiError("RT-AUTH-0003", "This key cannot write events")

    # ── 2. Rate limit ──────────────────────────────────────────────────
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise ApiError("RT-INGEST-0004", "Request body exceeds 5 MB")

    events = _parse_events(body)
    if len(events) > MAX_BATCH_SIZE:
        raise ApiError("RT-INGEST-0003", f"Batch exceeds {MAX_BATCH_SIZE} events")

    decision = await ratelimit.consume(
        redis, resolved.key_id, events=max(1, len(events)), now=time.time()
    )
    response.headers.update(decision.headers())
    if not decision.allowed:
        return error_response(
            "RT-RATE-0001",
            "Rate limit exceeded",
            headers={**decision.headers(), "Retry-After": str(decision.reset_seconds)},
        )

    # ── 3. Claim, atomically ───────────────────────────────────────────
    claim = None
    if idempotency_key:
        claim = await idempotency.claim(redis, resolved.project_id, idempotency_key)
        if claim.outcome is idempotency.Outcome.REPLAY:
            # Returned verbatim, re-inserting nothing.
            return claim.response
        if claim.outcome is idempotency.Outcome.IN_FLIGHT:
            raise ApiError("RT-CONFLICT-0004", "A duplicate of this request is already in flight")

    try:
        payload = await _persist(pool, resolved, events, redis)
    except Exception:
        # `03` §S1: the claim is deleted on any failure between the claim and
        # the response, so the client's retry can proceed. Leaving it would
        # make a transient database error look like a permanent 409.
        if idempotency_key:
            await idempotency.release(redis, resolved.project_id, idempotency_key)
        raise

    if idempotency_key:
        await idempotency.complete(redis, resolved.project_id, idempotency_key, payload)

    payload["meta"]["duration_ms"] = int((time.perf_counter() - started) * 1000)
    return payload


async def _resolve(redis: Any, pool: Any, key_hash: str) -> keys.ResolvedKey | None:
    """Cache-then-database, 60 s TTL (`03` §S1 step 1)."""
    import json

    cached = await redis.get(keys.cache_key(key_hash))
    if cached is not None:
        return keys.deserialise(json.loads(cached))

    async with pool.connection() as conn:
        resolved = await repository.resolve_key(conn, key_hash)

    if resolved is None:
        return None

    await redis.set(
        keys.cache_key(key_hash),
        json.dumps(keys.serialise(resolved), separators=(",", ":")),
        ex=keys.CACHE_TTL_SECONDS,
    )
    return resolved


def _parse_events(body: bytes) -> list[Any]:
    import json

    try:
        parsed = json.loads(body or b"{}")
    except ValueError as exc:
        raise ApiError("RT-VALIDATION-0001", "Request body is not valid JSON") from exc

    events = parsed.get("events") if isinstance(parsed, dict) else None
    if not isinstance(events, list):
        raise ApiError("RT-VALIDATION-0001", "events must be an array")
    return events


async def _persist(
    pool: Any, resolved: keys.ResolvedKey, events: list[Any], redis: Any
) -> dict[str, Any]:
    validation = validate_batch(events)

    if validation.all_invalid:
        raise ApiError(
            "RT-INGEST-0010",
            "Every event in the batch was invalid",
            details=[item.as_error() for item in validation.rejected],
        )

    # Step 6, before anything is persisted. The redactions travel with the row
    # so the UI can show THAT something was removed without storing what.
    accepted: list[ValidatedEvent] = []
    redactions: list[list[dict[str, str]]] = []
    for event in validation.accepted:
        cleaned, found = sanitise(event.payload)
        accepted.append(replace(event, payload=cleaned))
        redactions.append(found)

    batch_id = uuid.uuid4()
    # Pipelined: scoping the transaction costs two statements before the
    # insert, and at ~2 ms per round trip that is most of a 50 ms budget spent
    # on latency rather than work. Pipeline mode sends all three without
    # waiting for each result, which is safe because none of them reads a value
    # the next one needs.
    async with (
        pool.connection() as conn,
        conn.transaction(),
        conn.pipeline(),
    ):
        await repository.scope_to_project(conn, resolved.project_id)
        await repository.insert_events(
            conn,
            project_id=resolved.project_id,
            api_key_id=resolved.key_id,
            batch_id=batch_id,
            events=accepted,
        )

    if accepted:
        # Step 9. One job per accepted event; the worker fans out from here.
        await redis.rpush(INGEST_QUEUE, *[f"{resolved.project_id}:{batch_id}" for _ in accepted])

    logger.info(
        "events_received",
        project_id=resolved.project_id,
        batch_id=str(batch_id),
        accepted=len(accepted),
        rejected=len(validation.rejected),
    )

    return {
        "data": {
            "batch_id": f"bat_{batch_id.hex}",
            "accepted": len(accepted),
            "rejected": len(validation.rejected),
            "errors": [item.as_error() for item in validation.rejected],
        },
        "meta": {"request_id": current_request_id(), "duration_ms": 0},
    }
