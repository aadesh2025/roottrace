"""The HTTP transport (T2.5, `05` §5, §10).

> gzip; retries with exponential backoff on 5xx and 429

The classification of failures is the part with teeth: retrying a 4xx forever
means the buffer never drains and every subsequent event is dropped behind a
batch that can never succeed.
"""

from __future__ import annotations

import gzip
import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from roottrace_sdk._config import Config
from roottrace_sdk._transport import HttpTransport, backoff_delay

pytestmark = pytest.mark.unit

TEST_API_KEY = "rt_test_" + "0" * 32


@pytest.fixture
def config() -> Config:
    return Config(api_key=TEST_API_KEY, endpoint="http://127.0.0.1:1/v1/events")


class _Recorder:
    """Stands in for `urlopen`, capturing the request it was handed."""

    def __init__(self, status: int = 202, error: BaseException | None = None) -> None:
        self.status = status
        self.error = error
        self.request: Any = None

    def __call__(self, request: Any, timeout: float | None = None) -> Any:
        self.request = request
        if self.error is not None:
            raise self.error
        return _Response(self.status)


class _Response(io.BytesIO):
    def __init__(self, status: int) -> None:
        super().__init__(b"{}")
        self.status = status

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    import email.message

    headers = email.message.Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError("http://x/", code, "no", headers, None)


# ── What goes on the wire ──────────────────────────────────────────────────


def test_the_body_is_gzipped_json_under_an_events_key(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(urllib.request, "urlopen", recorder)

    HttpTransport(config).send([{"event_id": "evt_1"}], idempotency_key="k")

    assert recorder.request.get_header("Content-encoding") == "gzip"
    body = json.loads(gzip.decompress(recorder.request.data))
    assert body == {"events": [{"event_id": "evt_1"}]}


def test_the_key_travels_as_a_bearer_token(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(urllib.request, "urlopen", recorder)

    HttpTransport(config).send([], idempotency_key="k")

    assert recorder.request.get_header("Authorization") == f"Bearer {TEST_API_KEY}"


def test_the_idempotency_key_is_sent_verbatim(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(urllib.request, "urlopen", recorder)

    HttpTransport(config).send([], idempotency_key="550e8400-e29b-41d4-a716-446655440000")

    assert recorder.request.get_header("Idempotency-key") == (
        "550e8400-e29b-41d4-a716-446655440000"
    )


def test_the_user_agent_identifies_the_sdk(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(urllib.request, "urlopen", recorder)

    HttpTransport(config).send([], idempotency_key="k")

    assert recorder.request.get_header("User-agent", "").startswith("roottrace-python/")


def test_a_non_http_endpoint_is_refused_at_construction() -> None:
    """Belt and braces with `Config`: this is the line that actually puts the
    credential on the wire."""
    config = Config(api_key=TEST_API_KEY, endpoint="https://api.roottrace.ai/v1/events")
    object.__setattr__(config, "endpoint", "ftp://example.com/events")
    with pytest.raises(ValueError, match="scheme"):
        HttpTransport(config)


# ── Which failures are retryable ───────────────────────────────────────────


@pytest.mark.parametrize("status", [200, 202, 204])
def test_any_2xx_is_success(config: Config, monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """`05` §5 answers 202. A proxy that answers 200 has still taken the
    batch, and treating that as failure would resend it."""
    monkeypatch.setattr(urllib.request, "urlopen", _Recorder(status=status))
    result = HttpTransport(config).send([], idempotency_key="k")
    assert result.ok and not result.retryable


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_5xx_and_429_are_retryable(
    config: Config, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _Recorder(error=_http_error(status)))
    result = HttpTransport(config).send([], idempotency_key="k")
    assert not result.ok and result.retryable


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_a_4xx_is_not_retryable(
    config: Config, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """A revoked key or a malformed batch cannot be fixed by trying again, and
    a batch pinned at the head of the buffer blocks every event behind it."""
    monkeypatch.setattr(urllib.request, "urlopen", _Recorder(error=_http_error(status)))
    result = HttpTransport(config).send([], idempotency_key="k")
    assert not result.ok and not result.retryable


def test_an_unreachable_api_is_retryable(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    """`05` §10 answers this with the buffer, not with a lost batch."""
    monkeypatch.setattr(
        urllib.request, "urlopen", _Recorder(error=urllib.error.URLError("connection refused"))
    )
    result = HttpTransport(config).send([], idempotency_key="k")
    assert not result.ok and result.retryable


def test_a_timeout_is_retryable(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _Recorder(error=TimeoutError()))
    result = HttpTransport(config).send([], idempotency_key="k")
    assert not result.ok and result.retryable


def test_the_transport_never_raises_at_all(config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    """Not merely the known errors — anything at all. A TLS handshake failure
    or a broken socket file object must come back as a `SendResult`."""
    monkeypatch.setattr(
        urllib.request, "urlopen", _Recorder(error=RuntimeError("something unexpected"))
    )
    result = HttpTransport(config).send([], idempotency_key="k")
    assert not result.ok and result.retryable and result.detail == "RuntimeError"


# ── Backoff ────────────────────────────────────────────────────────────────


def test_retry_after_overrides_the_schedule(
    config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(urllib.request, "urlopen", _Recorder(error=_http_error(429, "7")))
    result = HttpTransport(config).send([], idempotency_key="k")
    assert result.retry_after == 7.0


@pytest.mark.parametrize("value", ["Wed, 21 Oct 2026 07:28:00 GMT", "-3", "soon", ""])
def test_an_unusable_retry_after_falls_back_to_the_schedule(
    config: Config, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The HTTP-date form is legal and deliberately not honoured: parsing it
    against a skewed client clock can produce an absurd or negative delay."""
    monkeypatch.setattr(urllib.request, "urlopen", _Recorder(error=_http_error(503, value)))
    assert HttpTransport(config).send([], idempotency_key="k").retry_after is None


def test_the_backoff_doubles() -> None:
    schedule = [backoff_delay(n, base=0.5, cap=30.0, retry_after=None) for n in range(1, 5)]
    assert schedule == [0.5, 1.0, 2.0, 4.0]


def test_the_backoff_is_capped() -> None:
    assert backoff_delay(20, base=0.5, cap=30.0, retry_after=None) == 30.0


def test_a_huge_retry_after_is_capped_too() -> None:
    """A server asking us to wait an hour would otherwise stall the buffer for
    an hour, dropping everything captured in the meantime."""
    assert backoff_delay(1, base=0.5, cap=30.0, retry_after=3600.0) == 30.0
