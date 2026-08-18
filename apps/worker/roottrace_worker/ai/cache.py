"""Deterministic-stage caching (`06` §2.4: "Deterministic stages (S4 on
identical input) cached by content hash, 1 h TTL").

Same shape as `apps/api/roottrace_api/ingest/idempotency.py`'s `RedisLike`:
typed structurally against the exact operations needed, not against a
client library, so a test can supply an in-memory double that is a real
implementation of the contract rather than a mock standing in for one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

#: `A3`: `RT_LLM_CACHE_TTL_SECONDS`, default 1 h.
DEFAULT_CACHE_TTL_SECONDS = 3600


class RedisLike(Protocol):
    async def get(self, name: str) -> str | bytes | None: ...

    async def set(self, name: str, value: str, *, ex: int | None = ...) -> bool | None: ...


@dataclass(frozen=True, slots=True)
class CachedCompletion:
    """What survives a cache round-trip — enough for `gateway.py` to
    reconstruct an `LLMResult` without a provider call, while still marking
    it as a cache hit (`tokens_in`/`tokens_out` are the *original* call's
    real counts, not zero — a cached call still cost tokens the first time,
    and `06` §8.3's per-stage cost profiling should see that, not a
    misleadingly free repeat)."""

    provider: str
    model: str
    raw_text: str
    tokens_in: int
    tokens_out: int


def cache_key(prompt_hash: str) -> str:
    """Namespaced so a cache flush for one purpose cannot collide with
    another Redis use on the same instance (idempotency keys use `rt:idem:`,
    this uses `rt:llmcache:`)."""
    return f"rt:llmcache:{prompt_hash}"


async def get_cached(redis: RedisLike, prompt_hash: str) -> CachedCompletion | None:
    raw = await redis.get(cache_key(prompt_hash))
    if raw is None:
        return None
    decoded = raw.decode() if isinstance(raw, bytes) else raw
    payload = json.loads(decoded)
    return CachedCompletion(**payload)


async def put_cached(
    redis: RedisLike,
    prompt_hash: str,
    completion: CachedCompletion,
    *,
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
) -> None:
    await redis.set(
        cache_key(prompt_hash),
        json.dumps(
            {
                "provider": completion.provider,
                "model": completion.model,
                "raw_text": completion.raw_text,
                "tokens_in": completion.tokens_in,
                "tokens_out": completion.tokens_out,
            },
            separators=(",", ":"),
        ),
        ex=ttl_seconds,
    )
