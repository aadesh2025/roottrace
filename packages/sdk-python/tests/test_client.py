"""The sender — batching, retry, buffering, shutdown (T2.5, `05` §10).

> batches up to 100 events or 5 s, whichever first; gzip; retries with
> exponential backoff on 5xx and 429; drops to a bounded local buffer (1,000
> events) if the API is unreachable

Every test here runs against a fake transport, so nothing touches a network and
nothing sleeps: the sender is woken by `flush()` and reports back through a
`Condition`. The end-to-end version, against a real socket that is then killed,
is `tests/integration/test_sdk_end_to_end.py`.
"""

from __future__ import annotations

import dataclasses
import threading
from typing import Any

import pytest

from roottrace_sdk._client import Client
from roottrace_sdk._config import Config
from roottrace_sdk._transport import SendResult

pytestmark = pytest.mark.unit

OK = SendResult(ok=True, retryable=False, status=202)
UNAVAILABLE = SendResult(ok=False, retryable=True, status=503, detail="HTTP 503")
UNREACHABLE = SendResult(ok=False, retryable=True, detail="URLError")
REJECTED = SendResult(ok=False, retryable=False, status=422, detail="HTTP 422")


def event(index: int) -> dict[str, Any]:
    return {"event_id": f"evt_{index:04d}"}


def ids(events: list[dict[str, Any]]) -> list[str]:
    return [str(item["event_id"]) for item in events]


def tuned(config: Config, **overrides: Any) -> Config:
    return dataclasses.replace(config, **overrides)


# ── Batching ───────────────────────────────────────────────────────────────


def test_a_batch_never_exceeds_one_hundred(config: Config, transport: Any) -> None:
    """`05` §5 answers 101 with `RT-INGEST-0003` and refuses the whole batch.

    Driven synchronously rather than through the thread so the split is exact:
    with the sender running, the boundary depends on when it happens to wake.
    """
    client = Client(config, transport)
    for index in range(250):
        client.buffer.add(event(index))

    client._send_pending()

    assert [len(batch) for batch, _ in transport.calls] == [100, 100, 50]
    assert ids(transport.sent_events) == ids([event(index) for index in range(250)])


def test_events_are_sent_oldest_first(config: Config, transport: Any) -> None:
    client = Client(config, transport)
    for index in range(5):
        client.buffer.add(event(index))

    client._send_pending()

    assert ids(transport.sent_events) == [
        "evt_0000",
        "evt_0001",
        "evt_0002",
        "evt_0003",
        "evt_0004",
    ]


def test_capture_delivers_through_the_sender_thread(config: Config, transport: Any) -> None:
    """The end-to-end path in-process: producer thread → buffer → sender."""
    client = Client(config, transport)
    for index in range(10):
        client.capture(event(index))

    assert client.flush(timeout=5.0)
    assert len(transport.sent_events) == 10
    client.close(timeout=1.0)


def test_the_sender_thread_starts_lazily_and_is_a_daemon(config: Config, transport: Any) -> None:
    """`init` is normally called at import time, before a pre-fork server
    forks — and threads do not survive `fork`. Starting on first use means each
    worker starts its own.

    Daemon, so a customer's process can exit even if the API is unreachable and
    a send is mid-backoff.
    """
    client = Client(config, transport)
    before = client._thread
    assert before is None, "the sender started before any event was captured"

    client.capture(event(0))

    thread = client._thread
    assert thread is not None
    assert thread.daemon
    client.close(timeout=1.0)


# ── Retry ──────────────────────────────────────────────────────────────────


def test_a_retry_reuses_the_batch_idempotency_key(config: Config, make_transport: Any) -> None:
    """`03` §S1 B7: a batch that timed out may well have been persisted. A
    fresh key on the retry lands it twice — duplicate `raw_events`, an inflated
    `occurrence_count`, and possibly a second paid pipeline run."""
    transport = make_transport(UNAVAILABLE, OK)
    client = Client(tuned(config, max_attempts=3), transport)
    client.buffer.add(event(0))

    client._send_pending()

    assert len(transport.calls) == 2
    assert transport.calls[0][1] == transport.calls[1][1]


def test_separate_batches_get_separate_keys(config: Config, transport: Any) -> None:
    """The opposite failure: one key for everything would make the second batch
    look like a replay of the first and be discarded by the server."""
    client = Client(config, transport)
    for index in range(150):
        client.buffer.add(event(index))

    client._send_pending()

    assert transport.calls[0][1] != transport.calls[1][1]


def test_retries_stop_at_max_attempts(config: Config, make_transport: Any) -> None:
    transport = make_transport(UNAVAILABLE)
    client = Client(tuned(config, max_attempts=3), transport)
    client.buffer.add(event(0))

    client._send_pending()

    assert len(transport.calls) == 3


def test_a_4xx_is_not_retried(config: Config, make_transport: Any) -> None:
    transport = make_transport(REJECTED)
    client = Client(tuned(config, max_attempts=5), transport)
    client.buffer.add(event(0))

    client._send_pending()

    assert len(transport.calls) == 1


# ── Buffering, which is the acceptance criterion ───────────────────────────


def test_an_unreachable_api_buffers_rather_than_losing_the_batch(
    config: Config, make_transport: Any, suppressed: list[tuple[str, BaseException]]
) -> None:
    """T2.5 verbatim: "Killing the API mid-run causes buffering, not a crash".

    Discarding here would be the easy implementation and would pass every test
    that only checks the host application survived.
    """
    transport = make_transport(UNREACHABLE)
    client = Client(tuned(config, max_attempts=2), transport)
    client.buffer.add(event(0))
    client.buffer.add(event(1))

    client._send_pending()

    assert ids(client.buffer.drain()) == ["evt_0000", "evt_0001"]
    assert any("buffered" in str(exc) for _, exc in suppressed)


def test_a_recovered_api_drains_what_was_buffered(config: Config, make_transport: Any) -> None:
    """The other half. A buffer that fills and never empties is a leak, not a
    feature."""
    transport = make_transport(UNREACHABLE, UNREACHABLE, OK)
    client = Client(tuned(config, max_attempts=2), transport)
    client.buffer.add(event(0))

    client._send_pending()
    assert len(client.buffer) == 1

    client._send_pending()
    assert len(client.buffer) == 0
    assert ids(transport.sent_events[-1:]) == ["evt_0000"]


def test_a_permanently_rejected_batch_is_dropped_not_buffered(
    config: Config, make_transport: Any, suppressed: list[tuple[str, BaseException]]
) -> None:
    """Leaving it at the head of the buffer would block every event behind it
    forever — a revoked key would silence the SDK permanently rather than for
    as long as the key is revoked."""
    transport = make_transport(REJECTED)
    client = Client(config, transport)
    client.buffer.add(event(0))

    client._send_pending()

    assert len(client.buffer) == 0
    assert any("dropped" in str(exc) for _, exc in suppressed)


def test_a_full_buffer_reports_the_drop(config: Config, transport: Any) -> None:
    client = Client(tuned(config, buffer_size=3), transport)
    accepted = [client.buffer.add(event(index)) for index in range(5)]

    assert accepted == [True, True, True, False, False]
    assert client.buffer.dropped == 2


# ── Shutdown ───────────────────────────────────────────────────────────────


def test_close_flushes_what_is_buffered(config: Config, transport: Any) -> None:
    client = Client(config, transport)
    for index in range(3):
        client.capture(event(index))

    client.close(timeout=5.0)

    assert len(transport.sent_events) == 3
    assert client._thread is None


def test_close_is_safe_to_call_twice(config: Config, transport: Any) -> None:
    client = Client(config, transport)
    client.capture(event(0))
    client.close(timeout=5.0)
    client.close(timeout=5.0)

    assert len(transport.sent_events) == 1


def test_close_reports_events_it_could_not_send(
    config: Config, make_transport: Any, suppressed: list[tuple[str, BaseException]]
) -> None:
    """A shutdown that silently discards is how an incident's last events —
    the ones from the crash that caused the restart — go missing."""
    transport = make_transport(UNREACHABLE)
    client = Client(tuned(config, max_attempts=1, flush_interval=30.0), transport)
    client.capture(event(0))

    client.close(timeout=0.2)

    assert any(where == "client.close" for where, _ in suppressed)


def test_flush_on_an_untouched_client_is_true_and_starts_nothing(
    config: Config, transport: Any
) -> None:
    client = Client(config, transport)
    assert client.flush(timeout=0.1) is True
    assert client._thread is None


# ── Surviving a fork ───────────────────────────────────────────────────────


def test_a_forked_child_does_not_inherit_a_dead_thread(
    config: Config, transport: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pre-fork server (gunicorn `--preload`, uwsgi) forks after `init`.

    Threads do not survive `fork`: the child inherits a `Client` whose sender
    thread object exists and claims to be alive, but no thread is running
    behind it. Without the pid check the child would buffer silently and drop
    everything once full — the single worst failure this SDK can have, because
    it looks exactly like an application with no errors.

    Simulated by changing the reported pid rather than by actually forking, so
    the test is deterministic and runs on Windows too.
    """
    client = Client(config, transport)
    client.capture(event(0))
    inherited = client._thread
    assert inherited is not None

    monkeypatch.setattr("os.getpid", lambda: client._pid + 1)
    client.capture(event(1))

    assert client._thread is not None
    assert client._thread is not inherited
    assert client._thread.is_alive()
    client.close(timeout=1.0)


def test_a_forked_child_does_not_resend_the_parents_buffer(
    config: Config, transport: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parent is still running and will send them itself. Sending them here
    too would duplicate every event queued at the moment of the fork."""
    client = Client(tuned(config, flush_interval=30.0), transport)
    client.buffer.add(event(0))
    client.buffer.add(event(1))

    client.reset_after_fork()

    assert len(client.buffer) == 0
    assert client._thread is None


def test_the_fork_hook_is_registered_where_the_platform_has_one() -> None:
    """POSIX only, and guarded — `os.register_at_fork` does not exist on
    Windows, where there is no `fork` to survive."""
    import os

    from roottrace_sdk._client import register_fork_hook

    register_fork_hook(lambda: None)  # must not raise on any platform
    assert hasattr(os, "fork") == hasattr(os, "register_at_fork")


# ── The guarantee, from inside the thread ──────────────────────────────────


def test_a_transport_that_raises_does_not_kill_the_sender(
    config: Config, suppressed: list[tuple[str, BaseException]]
) -> None:
    """`HttpTransport` returns a `SendResult` for everything, but a customer or
    a test can install something else. A thrown exception in the sender thread
    would end it silently and the SDK would buffer forever after."""

    class Exploding:
        def __init__(self) -> None:
            self.calls = 0

        def send(self, events: list[dict[str, Any]], *, idempotency_key: str) -> SendResult:
            self.calls += 1
            raise RuntimeError("transport is broken")

    transport = Exploding()
    client = Client(config, transport)
    client.capture(event(0))
    client.flush(timeout=0.5)

    assert transport.calls >= 1
    assert any(where == "client.send_loop" for where, _ in suppressed)
    assert client._thread is not None and client._thread.is_alive()
    # And the event is still there. The batch has already left the buffer by
    # the time the transport is called, so without the put-back a broken
    # transport would delete events rather than queue them.
    assert len(client.buffer) == 1
    client.close(timeout=0.2)


def test_the_sender_thread_is_not_left_running_after_close(config: Config, transport: Any) -> None:
    before = {thread.name for thread in threading.enumerate()}
    client = Client(config, transport)
    client.capture(event(0))
    client.close(timeout=5.0)

    remaining = {thread.name for thread in threading.enumerate()} - before
    assert "roottrace-sender" not in remaining
