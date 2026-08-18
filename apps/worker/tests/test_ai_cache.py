"""Deterministic-stage caching (`06` §2.4, T5.1)."""

from __future__ import annotations

import pytest

from roottrace_worker.ai.cache import CachedCompletion, get_cached, put_cached

pytestmark = pytest.mark.unit


class InMemoryRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def get(self, name: str) -> str | None:
        return self._store.get(name)

    async def set(self, name: str, value: str, *, ex: int | None = None) -> bool:
        self._store[name] = value
        return True


async def test_a_miss_returns_none() -> None:
    redis = InMemoryRedis()
    assert await get_cached(redis, "nonexistent-hash") is None


async def test_a_put_then_get_round_trips() -> None:
    redis = InMemoryRedis()
    completion = CachedCompletion(
        provider="anthropic",
        model="claude-haiku-4-5",
        raw_text='{"x": 1}',
        tokens_in=100,
        tokens_out=20,
    )
    await put_cached(redis, "hash1", completion)
    fetched = await get_cached(redis, "hash1")
    assert fetched == completion


async def test_different_hashes_do_not_collide() -> None:
    redis = InMemoryRedis()
    await put_cached(
        redis,
        "hash1",
        CachedCompletion(provider="a", model="m1", raw_text="one", tokens_in=1, tokens_out=1),
    )
    await put_cached(
        redis,
        "hash2",
        CachedCompletion(provider="a", model="m2", raw_text="two", tokens_in=2, tokens_out=2),
    )
    first = await get_cached(redis, "hash1")
    second = await get_cached(redis, "hash2")
    assert first is not None and first.raw_text == "one"
    assert second is not None and second.raw_text == "two"


async def test_bytes_from_redis_decode_correctly() -> None:
    """A real Redis client can return `bytes`, not `str` — `get_cached`
    must handle both."""

    class BytesRedis:
        async def get(self, name: str) -> bytes:
            return (
                b'{"provider": "a", "model": "m", "raw_text": "x", "tokens_in": 1, "tokens_out": 1}'
            )

        async def set(self, name: str, value: str, *, ex: int | None = None) -> bool:
            raise NotImplementedError

    fetched = await get_cached(BytesRedis(), "hash")
    assert fetched is not None
    assert fetched.raw_text == "x"
