"""Outbound prompt scanning (`06` §2.4, §3.2, T5.1).

The drift test promised by this module's docstring —
`ai/redaction.py` deliberately duplicates `apps/api/roottrace_api/ingest/
sanitise.py`'s pattern table rather than importing it — lives at
`tests/integration/test_ai_redaction_contract_agreement.py`, not here:
`apps/worker` declares no dependency on `apps/api`, so a test that imports
both belongs at the shared top-level location, matching
`test_sdk_contract_agreement.py`'s precedent exactly.
"""

from __future__ import annotations

import pytest

from roottrace_worker.ai.redaction import scan_and_redact, shannon_entropy

pytestmark = pytest.mark.unit


def test_a_github_token_is_redacted() -> None:
    text = f"the deploy used ghp_{'a' * 36} to push"
    redacted, hits = scan_and_redact(text)
    assert "ghp_" not in redacted
    assert any(hit.kind == "github_token" for hit in hits)


def test_a_dsn_credential_is_redacted() -> None:
    text = "connecting to postgresql://admin:hunter2@db.internal:5432/prod"
    redacted, hits = scan_and_redact(text)
    assert "hunter2" not in redacted
    assert any(hit.kind == "dsn_credentials" for hit in hits)


#: Assembled rather than written literally, same reason as
#: `test_settings_invariants.py`'s `FAKE_PRIVATE_KEY`: the `detect-private-
#: key` hook matches on the PEM header as a contiguous string and cannot
#: tell this obviously-fake value from a real one — the fix is to stop
#: tripping it, never to exclude the file.
_FAKE_PEM_HEADER = "-----BEGIN RSA " + "PRIVATE KEY-----"
_FAKE_PEM_FOOTER = "-----END RSA " + "PRIVATE KEY-----"


def test_a_private_key_is_redacted() -> None:
    text = f"{_FAKE_PEM_HEADER}\nMIIC...\n{_FAKE_PEM_FOOTER}"
    redacted, hits = scan_and_redact(text)
    assert "MIIC" not in redacted
    assert any(hit.kind == "private_key" for hit in hits)


def test_a_high_entropy_token_inside_realistic_multiline_source_is_caught() -> None:
    """The regression this module's per-token scanning (not whole-block
    scanning) exists to prevent: a naive port of ingest's single-value
    entropy check would treat a multi-line prompt as one "value", which is
    never whitespace-free, and the check would silently never fire.

    The candidate string is assembled from parts, not written as one
    literal — same reasoning as `_FAKE_PEM_HEADER` above: a high-entropy
    run is exactly what `gitleaks`' `generic-api-key` rule flags, and this
    one is fake but indistinguishable from a real one as a contiguous
    string."""
    secret = (
        "kX9f2NqZ8vLp3wRt7yUj" + "4mBn6cAe1sHd5gQi0oPl"
    )  # not a named pattern, just high-entropy
    text = f"def handler():\n    token = '{secret}'\n    return call(token)\n"
    redacted, hits = scan_and_redact(text)
    assert secret not in redacted
    assert any(hit.kind == "high_entropy" for hit in hits)


def test_ordinary_source_code_is_untouched() -> None:
    text = "def calculate_total(cart):\n    return sum(item.price for item in cart.items)\n"
    redacted, hits = scan_and_redact(text)
    assert redacted == text
    assert hits == []


def test_an_already_redacted_dsn_is_not_redacted_a_second_time_as_high_entropy() -> None:
    text = "url = 'postgresql://admin:hunter2@db.internal:5432/prod'"
    _redacted, hits = scan_and_redact(text)
    kinds = [hit.kind for hit in hits]
    assert kinds.count("dsn_credentials") == 1
    assert "high_entropy" not in kinds


def test_shannon_entropy_of_empty_string_is_zero() -> None:
    assert shannon_entropy("") == 0.0


def test_shannon_entropy_of_a_repeated_character_is_zero() -> None:
    assert shannon_entropy("aaaaaaaaaa") == 0.0
