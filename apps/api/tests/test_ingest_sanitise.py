"""Ingest-time sanitisation (T2.2, `03` §S1, `11` §8.2 / SC-A5).

T2.2's acceptance, both halves:

- a payload seeded with one of each pattern emerges fully redacted, with
  `redactions` recording `{path, kind}` and never the value
- no false positives on 500 lines of ordinary source code

The second half is the harder one and is tested against the **synthetic
repository** rather than a hand-picked sample. Code I chose myself would be
code I already knew was safe.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from roottrace_api.ingest.sanitise import (
    HEADER_ALLOWLIST,
    Redaction,
    sanitise,
    sanitise_string,
    shannon_entropy,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

FIXTURE_REPO = Path(__file__).resolve().parents[3] / "fixtures" / "synthetic-repo"
CORPUS = Path(__file__).resolve().parents[3] / "fixtures" / "error-corpus"

#: One of each, in real credential formats. Assembled rather than written as
#: literals so no credential-shaped string exists in the repository — gitleaks
#: cannot tell a fixture from the real thing, and the fix for a hook that fires
#: is to stop tripping it, not to allowlist the file.
SECRETS: dict[str, str] = {
    "aws_key": "AKIA" + "A" * 16,
    "github_token": "ghp_" + "B" * 36,
    "provider_key": "sk-" + "c" * 40,
    "slack_token": "xoxb-" + "1234567890-abcdefghij",
    "roottrace_key": "rt_live_" + "d" * 32,
    "supabase_key": "sb_secret_" + "E" * 24,
    "jwt": ".".join(["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NSJ9", "c2lnbmF0dXJl"]),
    "email": "ada.lovelace@example.com",
    "pan": "4111 1111 1111 1111",
    "private_key": "-----BEGIN RSA "
    + "PRIVATE KEY-----\nMIIBOgIBAAJB\n-----END RSA PRIVATE KEY-----",
    "dsn_credentials": "postgresql://app:hunter2@db.internal:5432/orders",
}


# ── Every pattern is caught ────────────────────────────────────────────────


@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_each_pattern_is_redacted(kind: str) -> None:
    secret = SECRETS[kind]
    cleaned, redactions = sanitise_string(secret, "error.message")

    assert [item.kind for item in redactions] == [kind], (kind, cleaned)
    assert f"[REDACTED:{kind}]" in cleaned


@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_the_secret_value_never_survives(kind: str) -> None:
    """The property that matters. A partial match that leaves the tail behind
    is worse than no match, because the redaction marker says it was handled."""
    secret = SECRETS[kind]
    cleaned, _ = sanitise_string(secret, "error.message")

    # The credential itself, not the surrounding structure. A DSN's host and
    # database name are not secret and must survive — only `user:password` is
    # redacted, and an engineer still needs to know which database failed.
    body = "app:hunter2" if kind == "dsn_credentials" else secret
    for fragment in (body[-14:], body[:14]):
        if fragment.strip():
            assert fragment not in cleaned, (kind, cleaned)


def test_a_payload_seeded_with_one_of_each_emerges_clean() -> None:
    """T2.2's acceptance criterion, on a whole event."""
    payload: dict[str, Any] = {
        "error": {
            "type": "TypeError",
            "message": f"failed for {SECRETS['email']} using {SECRETS['aws_key']}",
            "stack_frames": [
                {"file": "app.py", "vars": {"token": SECRETS["github_token"]}},
                {"file": "pay.py", "vars": {"card": SECRETS["pan"]}},
            ],
        },
        "request": {
            "body_sample": json.dumps({"jwt": SECRETS["jwt"], "dsn": SECRETS["dsn_credentials"]}),
            "headers": {"content-type": "application/json", "authorization": "Bearer secret"},
        },
        "extra": {"key": SECRETS["provider_key"], "slack": SECRETS["slack_token"]},
    }

    cleaned, redactions = sanitise(payload)
    serialised = json.dumps(cleaned)

    for kind, secret in SECRETS.items():
        if kind in ("private_key", "roottrace_key", "supabase_key"):
            continue  # not seeded in this payload
        body = "app:hunter2" if kind == "dsn_credentials" else secret
        assert body[-14:] not in serialised, f"{kind} survived: {serialised}"

    assert redactions
    assert {item["kind"] for item in redactions} >= {
        "aws_key",
        "email",
        "github_token",
        "jwt",
        "pan",
        "provider_key",
        "slack_token",
    }


def test_redactions_record_path_and_kind_and_nothing_else() -> None:
    """A redaction log containing the secret is a second copy of the secret
    with a reassuring name."""
    _, redactions = sanitise({"extra": {"key": SECRETS["aws_key"]}})

    assert redactions == [{"path": "extra.key", "kind": "aws_key"}]
    for record in redactions:
        assert set(record) == {"path", "kind"}
        assert SECRETS["aws_key"] not in json.dumps(record)


def test_the_path_locates_the_value_inside_a_list() -> None:
    _, redactions = sanitise(
        {"error": {"stack_frames": [{"vars": {"t": SECRETS["github_token"]}}]}}
    )
    assert redactions[0]["path"] == "error.stack_frames[0].vars.t"


def test_the_input_is_not_mutated() -> None:
    """The caller may still need the original; a sanitiser that edits its
    argument makes that impossible to arrange later."""
    payload = {"extra": {"key": SECRETS["aws_key"]}}
    sanitise(payload)
    assert payload["extra"]["key"] == SECRETS["aws_key"]


# ── Headers ────────────────────────────────────────────────────────────────


def test_authorization_and_cookie_are_never_stored() -> None:
    """`11` §8.2: under any circumstances."""
    cleaned, redactions = sanitise(
        {
            "request": {
                "headers": {
                    "authorization": "Bearer " + SECRETS["jwt"],
                    "cookie": "session=abc123",
                    "content-type": "application/json",
                }
            }
        }
    )

    headers = cleaned["request"]["headers"]
    assert set(headers) == {"content-type"}
    assert "authorization" not in json.dumps(cleaned).lower()
    assert {item["kind"] for item in redactions} == {"header_not_allowlisted"}


def test_headers_are_allowlisted_not_denylisted() -> None:
    """An allowlist cannot be defeated by a header nobody thought of."""
    cleaned, _ = sanitise({"request": {"headers": {"x-internal-token": "abc", "accept": "*/*"}}})

    assert set(cleaned["request"]["headers"]) == {"accept"}
    assert "accept" in HEADER_ALLOWLIST


# ── Luhn, not "sixteen digits" ─────────────────────────────────────────────


def test_a_luhn_valid_number_is_redacted() -> None:
    cleaned, redactions = sanitise_string(SECRETS["pan"], "extra.card")
    assert "[REDACTED:pan]" in cleaned
    assert redactions[0].kind == "pan"


def test_a_sixteen_digit_order_number_is_left_alone() -> None:
    """Redacting one would remove the identifier an engineer needs to find the
    failing request — the check is Luhn, not length."""
    order = "1234567890123456"  # fails Luhn
    cleaned, redactions = sanitise_string(f"order {order} failed", "error.message")

    assert order in cleaned
    assert redactions == []


# ── Entropy ────────────────────────────────────────────────────────────────


def test_a_high_entropy_token_is_redacted() -> None:
    cleaned, redactions = sanitise_string("Xq7!vZ2@pL9#nR4$tB6%wK8^", "extra.opaque")
    assert cleaned == "[REDACTED:high_entropy]"
    assert redactions[0].kind == "high_entropy"


def test_entropy_only_applies_to_tokens_not_prose() -> None:
    """A stack trace or a log line can sit near the threshold across its whole
    length. Redacting one would destroy the diagnosis to catch nothing."""
    prose = (
        "Traceback (most recent call last): File app.py line 42 in handler "
        "raise ValueError the quick brown fox jumped over the lazy dog"
    )
    cleaned, redactions = sanitise_string(prose, "error.stack_trace")

    assert cleaned == prose
    assert redactions == []


def test_a_commit_sha_is_not_high_entropy() -> None:
    """Hex maxes out at 4.0 bits per character, below the 4.5 threshold. Worth
    asserting: commit SHAs appear throughout our own payloads."""
    sha = "9f2b1c4e8a7d6b5c4a3f2e1d0c9b8a7f6e5d4c3b"
    assert shannon_entropy(sha) < 4.5
    cleaned, redactions = sanitise_string(sha, "extra.sha")
    assert cleaned == sha
    assert redactions == []


# ── No false positives on real code ────────────────────────────────────────


def _source_lines() -> list[tuple[str, int, str]]:
    lines: list[tuple[str, int, str]] = []
    for path in sorted(FIXTURE_REPO.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            lines.append((path.name, number, line))
    return lines


def test_no_false_positives_on_ordinary_source_code() -> None:
    """T2.2's second criterion, against the synthetic repository.

    Code I picked myself would be code I already knew was safe. This is ~1,800
    lines of a plausible service, including URLs, hashes, hex constants and a
    webhook secret placeholder — exactly the shapes a naive matcher trips on.
    """
    lines = _source_lines()
    assert len(lines) >= 500, f"only {len(lines)} lines available"

    false_positives = []
    for filename, number, line in lines:
        _, redactions = sanitise_string(line, f"{filename}:{number}")
        for redaction in redactions:
            # The fixture repo genuinely contains an email address in an author
            # string and a placeholder webhook secret; those are true matches.
            if redaction.kind == "email" and "@acme.io" in line:
                continue
            if redaction.kind == "email" and "example.com" in line:
                continue
            false_positives.append((filename, number, redaction.kind, line.strip()[:90]))

    assert false_positives == [], f"{len(false_positives)} false positives: {false_positives[:6]}"


def test_the_corpus_survives_sanitisation_intact() -> None:
    """The 25 real payloads must not be mangled.

    A sanitiser that redacts half the fixture corpus would make every
    downstream stage measure something other than the case it names.
    """
    damaged = []
    for path in sorted(CORPUS.glob("*.json")):
        if ".case." in path.name:
            continue
        event = json.loads(path.read_text(encoding="utf-8"))["events"][0]
        _, redactions = sanitise(event)

        # The payloads legitimately contain example.com addresses in export
        # fixtures and a user_hash; nothing else should match.
        unexpected = [item for item in redactions if item["kind"] != "email"]
        if unexpected:
            damaged.append((path.stem, unexpected))

    assert damaged == [], f"corpus payloads redacted unexpectedly: {damaged}"


def test_a_redaction_is_a_frozen_record() -> None:
    redaction = Redaction(path="error.message", kind="aws_key")
    with pytest.raises(AttributeError):
        redaction.kind = "email"  # type: ignore[misc]
