"""SC25 — secrets never in logs (`11` §8.3, `12` §2.3).

The control is that redaction is a **processor in the chain**, so the test is
not "does `redact()` work" but "can a secret reach the output through any log
path". Each case below is a different path: our logger, a nested structure, a
list, a stdlib logger from a third-party library, an exception message, and the
per-request line the middleware emits.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
import structlog

from roottrace_api.log import REDACTED, configure_logging, get_logger, redact
from roottrace_api.settings import Settings

#: `make_settings` comes from conftest.py as a fixture rather than an import —
#: `apps/api/tests` is not a package, so importing across test modules would
#: depend on pytest's sys.path insertion.
SettingsFactory = Callable[..., Settings]

pytestmark = [pytest.mark.unit, pytest.mark.security]

# Obviously-fake values in real credential FORMATS. The formats are what the
# patterns match; a plausible-looking random string instead would test nothing
# about whether a real key is caught.
#
# Every one is assembled rather than written as a literal, so no
# credential-shaped string exists in the file. gitleaks flagged the two that
# were literals, and it was right to: it cannot tell a fixture from the real
# thing, and the fix for a hook that fires is to stop tripping it rather than to
# allowlist the file — an allowlist would also cover whatever is pasted here
# next.
SECRETS: dict[str, str] = {
    "ingest_key": "rt_live_" + "a" * 32,
    "github_token": "ghp_" + "B" * 36,
    "github_fine_grained": "github_pat_" + "C" * 40,
    "aws_key_id": "AKIA" + "D" * 16,
    "provider_key": "sk-" + "e" * 40,
    "slack_token": "xoxb-" + "1234567890-abcdefghij",
    "supabase_secret": "sb_secret_" + "F" * 24,
    "jwt": ".".join(["eyJhbGciOiJFUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "c2lnbmF0dXJl"]),
    "dsn_password": "postgresql://postgres:hunter2-REPLACE_ME@db.example.test:5432/postgres",
}


@pytest.fixture
def captured(make_settings: SettingsFactory) -> io.StringIO:
    """A real configured chain writing to a buffer.

    Not a stub: the thing under test is the configuration, so a test that
    called the processors directly would pass even if `configure_logging` never
    installed them.
    """
    stream = io.StringIO()
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
    configure_logging(make_settings(), stream=stream)
    return stream


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


@pytest.mark.parametrize("name", sorted(SECRETS))
def test_a_secret_never_reaches_the_output_by_value(name: str, captured: io.StringIO) -> None:
    """Case 1 — a credential in an innocuously named field.

    `token=...` would be caught by the key rule alone. These are logged under
    `detail`, so only the value patterns can save them, which is the case that
    matters: nobody names the field `password` when they leak one.
    """
    secret = SECRETS[name]
    get_logger("test").info("something_happened", detail=secret)
    assert secret not in captured.getvalue()


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "api-key",
        "apikey",
        "token",
        "refresh_token",
        "secret",
        "client_secret",
        "password",
        "authorization",
        "cookie",
        "private_key",
        "dsn",
        "access_key",
        "AUTHORIZATION",
    ],
)
def test_a_secret_named_field_is_redacted_whatever_it_holds(
    key: str, captured: io.StringIO
) -> None:
    """Case 2 — the key rule, which does not depend on recognising a format."""
    get_logger("test").info("config_loaded", **{key: "unrecognisable-value-12345"})
    output = captured.getvalue()
    assert "unrecognisable-value-12345" not in output
    assert REDACTED in output


def test_a_secret_nested_in_a_list_is_redacted() -> None:
    """`11` §8.3's reference implementation walks dicts and strings only, so a
    secret inside a list passed straight through it.

    This is the same failure shape as a security hook that exits 0 without
    scanning: present, and doing nothing. Fails before the list branch was
    added; the doc is corrected to match.
    """
    record = {"providers": [{"name": "github", "token": "ghp_" + "Z" * 36}]}
    assert redact(record) == {"providers": [{"name": "github", "token": REDACTED}]}


def test_a_secret_in_a_tuple_of_headers_is_redacted() -> None:
    """ASGI carries headers as a list of pairs, which is where an
    `Authorization` value would realistically arrive."""
    headers = [("content-type", "application/json"), ("authorization", "Bearer " + "t" * 40)]
    assert redact({"headers": headers}) == {
        "headers": [["content-type", "application/json"], ["authorization", REDACTED]]
    }


def test_a_secret_logged_by_a_third_party_library_is_redacted(captured: io.StringIO) -> None:
    """Case 3 — stdlib logging.

    httpx, uvicorn and friends do not use structlog. A redaction filter that
    only covered our own logger would miss the most likely leak of all: a
    connection string in somebody else's error message.
    """
    logging.getLogger("httpx").warning("connecting to %s", SECRETS["dsn_password"])
    output = captured.getvalue()
    assert "hunter2-REPLACE_ME" not in output
    assert REDACTED in output


def test_a_secret_in_an_exception_traceback_is_redacted(captured: io.StringIO) -> None:
    """Case 4 — the traceback.

    Redaction runs after `format_exc_info`, so the rendered exception text is
    scanned too. An exception carrying a connection string is the single most
    ordinary way a credential reaches a log.
    """
    try:
        raise RuntimeError(f"could not connect to {SECRETS['dsn_password']}")
    except RuntimeError as exc:
        get_logger("test").error("boom", exc_info=exc)

    output = captured.getvalue()
    assert "hunter2-REPLACE_ME" not in output


def test_the_configuration_is_what_redacts_not_the_call_site(
    captured: io.StringIO, make_settings: SettingsFactory
) -> None:
    """`12` §2.3: a developer writing `logger.info("config", **settings)` must
    not be able to bypass it. That exact line, with the whole settings object
    splatted in."""
    settings = make_settings()
    get_logger("test").info("config", **settings.model_dump())

    output = captured.getvalue()
    assert "postgres:postgres" not in output
    assert REDACTED in output


def test_ordinary_content_survives(captured: io.StringIO) -> None:
    """The other half. A redactor that mangles ordinary text destroys the
    evidence an operator is reading, so the patterns match credential formats
    rather than credential-shaped strings.

    A commit SHA, a stack frame and a file path are all high-entropy or
    punctuation-heavy, and an entropy-based rule would eat them — which is why
    this differs from the ingest-time sanitiser (`03` §S1).
    """
    message = (
        "applied 4f9d2c1a8b3e5f7091a2b3c4d5e6f708 at "
        "apps/api/roottrace_api/main.py:64 in create_app()"
    )
    get_logger("test").info("patch_applied", detail=message)
    assert _lines(captured)[-1]["detail"] == message


def test_every_line_carries_the_standard_fields(captured: io.StringIO) -> None:
    """`12` §2.1. These are what make a line joinable across services."""
    get_logger("test").info("something_happened")
    line = _lines(captured)[-1]

    assert line["message"] == "something_happened"
    assert line["level"] == "info"
    assert line["logger"] == "test"
    assert line["service"] == "api"
    assert line["version"] == "test"
    assert line["environment"] == "local"
    # ISO-8601 UTC with milliseconds, exactly as the contract shows.
    assert line["timestamp"].endswith("Z")
    assert len(line["timestamp"]) == len("2026-08-04T09:15:43.326Z")
