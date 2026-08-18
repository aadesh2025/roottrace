"""Prompt/response persistence (`06` §2.4: "Full prompt + response
persisted to object storage, referenced from `llm_calls`") — `04` §8's
`prompt_url`/`response_url` are `not null`, so every call must produce both
before the row can be written.

**One bucket, path-prefixed — not a dedicated bucket for this data.** `03`
§S1 step 8 is the precedent: raw ingest payloads go to
`raw/{project_id}/{yyyy}/{mm}/{dd}/{event_id}.json.gz` inside the single
configured bucket (`A3`: `RT_STORAGE_BUCKET`, default `roottrace-artifacts`),
not a bucket of their own. This module follows the same convention —
`PATH_PREFIX = "prompts"` inside that one bucket — rather than inventing a
second bucket nothing else in the spec names.

A direct REST call against Supabase Storage's S3-compatible object API via
`httpx`, not the `supabase-py` SDK — see `apps/worker/pyproject.toml`'s
comment on the `httpx` dependency for why (one PUT does not need
gotrue/postgrest/realtime/storage3 as transitive dependents)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx

#: `A3`: `RT_STORAGE_BUCKET`'s documented default.
DEFAULT_BUCKET = "roottrace-artifacts"

#: This module's own slice of the shared bucket — see module docstring.
PATH_PREFIX = "prompts"


class ObjectStore(Protocol):
    async def put(self, path: str, content: str, *, content_type: str = "application/json") -> str:
        """Writes `content` to `{PATH_PREFIX}/{path}` and returns the URL
        `llm_calls.prompt_url`/`response_url` should store."""
        ...


class SupabaseObjectStore:
    def __init__(
        self,
        *,
        supabase_url: str,
        service_role_key: str,
        bucket: str = DEFAULT_BUCKET,
        timeout_s: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = supabase_url.rstrip("/")
        self._service_role_key = service_role_key
        self._bucket = bucket
        self._timeout_s = timeout_s
        #: Injectable so a test can supply `httpx.MockTransport` and
        #: exercise real URL/header/body construction with no network.
        self._transport = transport

    def _url(self, path: str) -> str:
        return f"{self._base_url}/storage/v1/object/{self._bucket}/{PATH_PREFIX}/{path}"

    async def put(self, path: str, content: str, *, content_type: str = "application/json") -> str:
        url = self._url(path)
        async with httpx.AsyncClient(timeout=self._timeout_s, transport=self._transport) as client:
            response = await client.post(
                url,
                content=content.encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self._service_role_key}",
                    "Content-Type": content_type,
                    # Overwrite rather than 409 — a retried call after a
                    # partial failure writes the same path with the same
                    # content, and an object store that refuses the retry
                    # would turn "safe to retry" into "must not retry".
                    "x-upsert": "true",
                },
            )
            response.raise_for_status()
        return url


@dataclass(slots=True)
class InMemoryObjectStore:
    """For tests — a real implementation of the `ObjectStore` contract with
    no network, so `gateway.py`'s orchestration tests do not need `httpx`
    mocking to exercise the "prompt/response are persisted before the
    `llm_calls` row is written" ordering."""

    written: dict[str, str] = field(default_factory=dict)

    async def put(self, path: str, content: str, *, content_type: str = "application/json") -> str:
        self.written[path] = content
        return f"memory://{DEFAULT_BUCKET}/{PATH_PREFIX}/{path}"
