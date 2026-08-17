"""SDK configuration (T2.5, `05` §2.1 and §10)."""

from __future__ import annotations

from typing import Any

import pytest

from roottrace_sdk._config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_BUFFER_SIZE,
    DEFAULT_ENDPOINT,
    DEFAULT_FLUSH_INTERVAL,
    DEFAULT_MAX_BREADCRUMBS,
    ENV_API_KEY,
    ENV_ENDPOINT,
    Config,
    ConfigError,
    build,
)

pytestmark = pytest.mark.unit

#: Valid per `05` §2.1 and deliberately obviously fake.
TEST_API_KEY = "rt_test_" + "0" * 32


# ── The numbers `05` §10 fixes ─────────────────────────────────────────────


def test_the_defaults_are_the_ones_the_specification_names() -> None:
    """ "batches up to 100 events or 5 s ... a bounded local buffer (1,000
    events) ... max_breadcrumbs=25". Pinned so a tuning change has to be a
    deliberate edit to a test that says where the number came from."""
    assert DEFAULT_BATCH_SIZE == 100
    assert DEFAULT_FLUSH_INTERVAL == 5.0
    assert DEFAULT_BUFFER_SIZE == 1000
    assert DEFAULT_MAX_BREADCRUMBS == 25

    config = Config(api_key=TEST_API_KEY)
    assert config.batch_size == 100
    assert config.flush_interval == 5.0
    assert config.buffer_size == 1000
    assert config.max_breadcrumbs == 25
    assert config.sample_rate == 1.0
    assert config.endpoint == DEFAULT_ENDPOINT


def test_the_batch_size_cannot_exceed_the_servers_limit() -> None:
    """101 is not a tuning choice — `05` §5 answers it with `RT-INGEST-0003`
    and the whole batch is refused."""
    with pytest.raises(ConfigError, match="batch_size"):
        Config(api_key=TEST_API_KEY, batch_size=101)


# ── The API key ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key",
    [
        "rt_live_" + "a" * 32,
        "rt_test_" + "0123456789abcdef" * 2,
    ],
)
def test_a_well_formed_key_is_accepted(key: str) -> None:
    assert Config(api_key=key).api_key == key


@pytest.mark.parametrize(
    "key",
    [
        "",
        "rt_live_tooshort",
        "rt_prod_" + "a" * 32,
        "rt_live_" + "A" * 32,  # `05` §2.1 says hex, and hex here is lower-case
        "sk-proj-not-ours",
        "Bearer rt_live_" + "a" * 32,
    ],
)
def test_a_malformed_key_is_refused_at_init(key: str) -> None:
    """Refused rather than reported-and-continued.

    A malformed key produces 401s; the transport correctly refuses to retry a
    4xx; every event is then discarded. The developer sees an application with
    no errors, which is the failure mode with the longest time-to-discovery.
    """
    with pytest.raises(ConfigError, match="api_key"):
        Config(api_key=key)


def test_the_key_can_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_API_KEY, TEST_API_KEY)
    assert build().api_key == TEST_API_KEY


def test_an_explicit_key_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_API_KEY, "rt_live_" + "b" * 32)
    assert build(api_key=TEST_API_KEY).api_key == TEST_API_KEY


def test_no_key_anywhere_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_API_KEY, raising=False)
    with pytest.raises(ConfigError, match=ENV_API_KEY):
        build()


# ── The endpoint ───────────────────────────────────────────────────────────


def test_plain_http_to_a_real_host_is_refused() -> None:
    """The API key travels in an `Authorization` header on every request, and
    `05` §1 fixes the transport as HTTPS only."""
    with pytest.raises(ConfigError, match="https"):
        Config(api_key=TEST_API_KEY, endpoint="http://api.roottrace.ai/v1/events")


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:8000/v1/events",
        "http://127.0.0.1:8000/v1/events",
        "https://api.roottrace.ai/v1/events",
    ],
)
def test_https_and_loopback_are_allowed(endpoint: str) -> None:
    """Loopback is exempt so a developer can run against a local API. The
    exemption is by hostname, so it can never cover a real network hop."""
    assert Config(api_key=TEST_API_KEY, endpoint=endpoint).endpoint == endpoint


def test_a_non_http_scheme_is_refused() -> None:
    with pytest.raises(ConfigError, match="https"):
        Config(api_key=TEST_API_KEY, endpoint="file:///tmp/events.json")


def test_the_endpoint_can_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_ENDPOINT, "http://127.0.0.1:9999/v1/events")
    assert build(api_key=TEST_API_KEY).endpoint == "http://127.0.0.1:9999/v1/events"


# ── Everything else ────────────────────────────────────────────────────────


@pytest.mark.parametrize("rate", [-0.1, 1.1, 2.0])
def test_sample_rate_outside_zero_to_one_is_refused(rate: float) -> None:
    with pytest.raises(ConfigError, match="sample_rate"):
        Config(api_key=TEST_API_KEY, sample_rate=rate)


def test_an_unknown_environment_is_refused() -> None:
    """`04` §7's `environment_kind` is the authority; the server answers
    anything else with `RT-INGEST-0013`, per event."""
    with pytest.raises(ConfigError, match="environment"):
        Config(api_key=TEST_API_KEY, environment="prod")


def test_a_mistyped_option_is_an_error_not_a_silent_default() -> None:
    """`max_breadcrumb=5` would otherwise leave the default in place and look
    exactly like the SDK ignoring the setting."""
    with pytest.raises(ConfigError, match="max_breadcrumb"):
        build(api_key=TEST_API_KEY, max_breadcrumb=5)


def test_the_config_is_frozen() -> None:
    """The sender thread reads it without a lock."""
    config = Config(api_key=TEST_API_KEY)
    with pytest.raises((AttributeError, TypeError)):
        config.sample_rate = 0.5  # type: ignore[misc]


@pytest.mark.parametrize(
    ("options", "match"),
    [
        ({"buffer_size": 0}, "buffer_size"),
        ({"flush_interval": 0.0}, "flush_interval"),
        ({"flush_interval": -1.0}, "flush_interval"),
        ({"max_breadcrumbs": -1}, "max_breadcrumbs"),
        ({"max_attempts": 0}, "max_attempts"),
        ({"backoff_base": 0.0}, "backoff"),
        ({"backoff_cap": -1.0}, "backoff"),
    ],
)
def test_every_remaining_bound_is_checked(options: dict[str, Any], match: str) -> None:
    """A zero `flush_interval` busy-loops the sender thread; a zero
    `max_attempts` never sends at all. Both look like configuration and behave
    like an outage, so neither is accepted."""
    with pytest.raises(ConfigError, match=match):
        Config(api_key=TEST_API_KEY, **options)
