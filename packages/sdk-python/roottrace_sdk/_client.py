"""The sender — batching, retry, buffering, shutdown (`05` §10).

> batches up to 100 events or 5 s, whichever first

One background daemon thread owns every network call. The host application's
threads only ever append to a bounded, lock-protected buffer, which is what
keeps `capture_exception` cheap in a request path that is already failing.

Three things here are less obvious than the batching:

**The thread starts lazily, on the first event — not in `init`.** `init` is
normally called at import time, and a pre-fork server (gunicorn `--preload`,
uwsgi) forks after that. Threads do not survive `fork`: the child would hold a
`Client` whose sender thread does not exist, and would buffer silently until it
dropped everything. Starting on first use means each worker starts its own, and
`os.register_at_fork` resets the state a child inherits.

**Retries reuse the batch's idempotency key.** A batch that timed out may well
have been persisted; a fresh key on the retry would duplicate it, inflate
`occurrence_count`, and — per `03` §S1's B7 note — potentially buy a second
paid pipeline run.

**Every wait is on an `Event` or a `Condition`, never `time.sleep`.** A process
shutting down in the middle of a thirty-second backoff must not wait thirty
seconds for it, and `flush()` must return the moment the buffer drains rather
than at the end of a polling tick.
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any

from roottrace_sdk import _guard
from roottrace_sdk._buffer import EventBuffer
from roottrace_sdk._config import Config
from roottrace_sdk._transport import HttpTransport, SendResult, backoff_delay


class Client:
    """Owns the buffer and the sender thread for one `init`."""

    def __init__(self, config: Config, transport: Any | None = None) -> None:
        self._config = config
        self._transport = transport if transport is not None else HttpTransport(config)
        self._buffer = EventBuffer(config.buffer_size)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._drained = threading.Condition()
        self._sending = False
        self._thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        self._pid = os.getpid()

    # ── Producer side ──────────────────────────────────────────────────

    @property
    def buffer(self) -> EventBuffer:
        return self._buffer

    def capture(self, event: dict[str, Any]) -> bool:
        """Enqueue one event. Returns False if the buffer was full."""
        self._ensure_thread()
        accepted = self._buffer.add(event)
        if len(self._buffer) >= self._config.batch_size:
            self._wake.set()
        return accepted

    def flush(self, timeout: float = 2.0) -> bool:
        """Send everything buffered. True if the buffer emptied in time.

        Returning a bool rather than raising on timeout is the never-raises
        rule applied to shutdown: a host application calling `flush()` from a
        lifespan handler must not have its shutdown fail because ours did.
        """
        if self._thread is None and len(self._buffer) == 0:
            return True
        self._ensure_thread()
        self._wake.set()
        with self._drained:
            return self._drained.wait_for(self._is_drained, timeout)

    def close(self, timeout: float = 2.0) -> None:
        if not self.flush(timeout):
            _guard.report(
                "client.close",
                RuntimeError(f"{len(self._buffer)} event(s) unsent at shutdown"),
            )
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        self._thread = None

    def _is_drained(self) -> bool:
        return not self._sending and len(self._buffer) == 0

    # ── Sender side ────────────────────────────────────────────────────

    def _ensure_thread(self) -> None:
        if self._pid != os.getpid():
            # Inherited across a fork without `register_at_fork` having run
            # (it is POSIX-only). Whatever the parent had is not ours.
            self.reset_after_fork()
        if self._thread is not None and self._thread.is_alive():
            return
        with self._thread_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            # Daemon, so a customer's process can exit even if the API is
            # unreachable and a send is mid-backoff. The `atexit` hook in
            # `__init__.py` gets the chance to flush first; the daemon flag is
            # the guarantee that a failure to flush cannot wedge their exit.
            thread = threading.Thread(target=self._run, name="roottrace-sender", daemon=True)
            self._thread = thread
            thread.start()

    def reset_after_fork(self) -> None:
        self._pid = os.getpid()
        self._thread = None
        self._thread_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._drained = threading.Condition()
        self._sending = False
        # The buffer's contents came from the parent, which is still running
        # and will send them itself. Sending them here too would duplicate
        # every event the parent had queued at the moment of the fork.
        self._buffer = EventBuffer(self._config.buffer_size)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self._config.flush_interval)
            self._wake.clear()
            with _guard.suppressed("client.send_loop"):
                self._send_pending()
        with _guard.suppressed("client.send_loop"):
            self._send_pending()

    def _send_pending(self) -> None:
        while True:
            batch = self._buffer.take(self._config.batch_size)
            if not batch:
                self._announce_drained()
                return
            with self._drained:
                self._sending = True
            try:
                keep_going = self._send_with_retry(batch)
            except Exception:
                # `HttpTransport` returns a `SendResult` for everything, but a
                # replacement transport can raise. The batch has already left
                # the buffer by this point, so without this it is lost — a
                # broken transport would silently delete events rather than
                # queue them.
                self._buffer.put_back(batch)
                raise
            finally:
                with self._drained:
                    self._sending = False
                    self._drained.notify_all()
            if not keep_going:
                # The batch went back to the buffer because the API is
                # unreachable. Taking it again straight away would be a hot
                # loop through the whole retry schedule with no pause between
                # rounds; returning lets the next `flush_interval` tick or an
                # explicit `flush()` decide when to try again.
                return

    def _announce_drained(self) -> None:
        with self._drained:
            self._sending = False
            self._drained.notify_all()

    def _send_with_retry(self, batch: list[dict[str, Any]]) -> bool:
        """Send one batch. False means the batch is back in the buffer."""
        idempotency_key = str(uuid.uuid4())
        result = SendResult(ok=False, retryable=True)

        for attempt in range(1, self._config.max_attempts + 1):
            result = self._transport.send(batch, idempotency_key=idempotency_key)
            if result.ok or not result.retryable:
                break
            if attempt == self._config.max_attempts:
                break
            delay = backoff_delay(
                attempt,
                base=self._config.backoff_base,
                cap=self._config.backoff_cap,
                retry_after=result.retry_after,
            )
            if self._stop.wait(delay):
                # Shutting down mid-backoff. Put the batch back rather than
                # spending the remaining schedule on a process that is leaving.
                self._buffer.put_back(batch)
                return False

        if result.ok:
            return True

        if result.retryable:
            # Still unreachable after the full schedule. Back to the buffer —
            # this is the "killing the API causes buffering" behaviour, and the
            # reason the events are not simply discarded here.
            self._buffer.put_back(batch)
            _guard.report(
                "client.send",
                ConnectionError(f"buffered {len(batch)} event(s): {result.detail}"),
            )
            return False

        # A 4xx we cannot fix by trying again. Dropping is deliberate: leaving
        # a permanently-rejected batch at the head of the buffer would block
        # every event behind it forever.
        _guard.report(
            "client.send",
            RuntimeError(f"dropped {len(batch)} event(s): {result.detail}"),
        )
        return True


def register_fork_hook(get_client: Any) -> None:
    """Reset the inherited client in a forked child (POSIX only)."""
    if not hasattr(os, "register_at_fork"):  # pragma: no cover - Windows
        return

    def after_in_child() -> None:
        client = get_client()
        if client is not None:
            client.reset_after_fork()

    os.register_at_fork(after_in_child=after_in_child)
