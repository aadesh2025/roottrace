"""Shared fixtures for the SDK suite.

Two things every test here needs: a `Config` that is valid but never reaches a
network, and a way to see what the never-raises guard swallowed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest

import roottrace_sdk
from roottrace_sdk import _breadcrumbs, _guard
from roottrace_sdk._config import Config
from roottrace_sdk._transport import SendResult

#: Valid per `05` §2.1 and deliberately obviously fake.
TEST_API_KEY = "rt_test_" + "0" * 32


@pytest.fixture
def config() -> Config:
    """Fast enough that a retry schedule fits inside a test, and pointed at a
    loopback endpoint no test ever actually connects to."""
    return Config(
        api_key=TEST_API_KEY,
        endpoint="http://127.0.0.1:1/v1/events",
        environment="production",
        service="checkout-api",
        release="v2.14.3",
        flush_interval=0.05,
        backoff_base=0.001,
        backoff_cap=0.01,
        max_attempts=2,
    )


class FakeTransport:
    """Records what would have been sent, and answers however a test wants.

    `results` is consumed one per `send`; the last one repeats. Recording the
    idempotency key alongside the batch is what lets a test assert that a retry
    reuses it rather than minting a new one.
    """

    def __init__(self, *results: SendResult) -> None:
        self.results = list(results) or [SendResult(ok=True, retryable=False, status=202)]
        self.calls: list[tuple[list[dict[str, Any]], str]] = []

    def send(self, events: list[dict[str, Any]], *, idempotency_key: str) -> SendResult:
        self.calls.append(([dict(event) for event in events], idempotency_key))
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]

    @property
    def sent_events(self) -> list[dict[str, Any]]:
        return [event for batch, _ in self.calls for event in batch]


@pytest.fixture
def transport() -> FakeTransport:
    """A transport that accepts everything."""
    return FakeTransport()


@pytest.fixture
def make_transport() -> Callable[..., FakeTransport]:
    """Build a transport with a scripted sequence of results."""
    return FakeTransport


@pytest.fixture
def suppressed() -> Iterator[list[tuple[str, BaseException]]]:
    """Everything the never-raises guard swallowed during the test.

    Without this, "the call did not raise" is satisfied just as well by code
    that never ran. Tests assert on both halves.
    """
    seen: list[tuple[str, BaseException]] = []
    remove = _guard.add_sink(lambda where, exc: seen.append((where, exc)))
    try:
        yield seen
    finally:
        remove()


@pytest.fixture(autouse=True)
def _reset_sdk_state() -> Iterator[None]:
    """The SDK's module state is global, as its API requires. Reset it around
    every test so ordering cannot matter (`14` §3: tests pass in any order)."""
    _breadcrumbs.clear()
    yield
    roottrace_sdk.close(timeout=0.5)
    _breadcrumbs.clear()
    _guard.set_debug(False)
