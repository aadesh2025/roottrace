"""Prompt/response object storage (`06` §2.4, T5.1).

`SupabaseObjectStore` is tested against `httpx.MockTransport` — a real
transport-level double, not a mock of the class itself, so the test
actually exercises URL construction, headers, and body encoding rather than
asserting that a mock was called with some arguments."""

from __future__ import annotations

import httpx
import pytest

from roottrace_worker.ai.storage import (
    DEFAULT_BUCKET,
    PATH_PREFIX,
    InMemoryObjectStore,
    SupabaseObjectStore,
)

pytestmark = pytest.mark.unit


async def test_in_memory_store_round_trips_content() -> None:
    store = InMemoryObjectStore()
    url = await store.put("ctx1/prompt.json", '{"x": 1}')
    assert store.written["ctx1/prompt.json"] == '{"x": 1}'
    assert "ctx1/prompt.json" in url


async def test_supabase_store_puts_to_the_correct_path_with_auth_and_upsert() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"Key": "ok"})

    store = SupabaseObjectStore(
        supabase_url="https://example.supabase.co",
        service_role_key="secret-key",
        transport=httpx.MockTransport(handler),
    )

    url = await store.put("hash123/attempt-1/prompt.json", '{"a": 1}')

    request = captured["request"]
    assert request.method == "POST"
    assert str(request.url) == (
        f"https://example.supabase.co/storage/v1/object/{DEFAULT_BUCKET}/{PATH_PREFIX}"
        "/hash123/attempt-1/prompt.json"
    )
    assert request.headers["authorization"] == "Bearer secret-key"
    assert request.headers["x-upsert"] == "true"
    assert request.content == b'{"a": 1}'
    assert url.endswith("hash123/attempt-1/prompt.json")


async def test_a_custom_bucket_overrides_the_default() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"Key": "ok"})

    store = SupabaseObjectStore(
        supabase_url="https://example.supabase.co",
        service_role_key="secret-key",
        bucket="a-different-bucket",
        transport=httpx.MockTransport(handler),
    )

    await store.put("x.json", "{}")
    assert "/object/a-different-bucket/prompts/x.json" in str(captured["request"].url)


async def test_supabase_store_raises_on_a_non_2xx_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    store = SupabaseObjectStore(
        supabase_url="https://example.supabase.co",
        service_role_key="secret-key",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(httpx.HTTPStatusError):
        await store.put("x.json", "{}")
