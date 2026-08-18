"""Outbound prompt scanning (`06` §2.4, §3.2: "Same patterns as ingest; any
hit is redacted before transmission").

**Deliberately duplicated from `apps/api/roottrace_api/ingest/sanitise.py`,
not imported from it.** `apps/worker` declares no dependency on
`apps/api` — they are separate deployables with separate privilege
boundaries (`CLAUDE.md`: workers run as `service_role`), and the two
packages have never depended on each other. The same tradeoff the SDK
already made for the same reason (`PROJECT-STATUS.md` §4: "the SDK declares
zero runtime dependencies... the cost is three things duplicated from
`apps/api`") applies here: duplication plus a drift test beats a new
cross-service dependency. `test_redaction_matches_ingest_patterns` is that
drift test.

Narrower in scope than the ingest sanitiser on purpose: this scans *rendered
prompt text* (source code, breadcrumbs, error messages), not a structured
HTTP payload, so there is no header allowlist and no per-field walk — just
the same pattern table and entropy check run once over the whole string."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

#: Same threshold and patterns as `apps/api/roottrace_api/ingest/sanitise.py`
#: — keep the two in sync; `test_redaction_matches_ingest_patterns` fails
#: the build if they drift.
ENTROPY_THRESHOLD = 4.5
ENTROPY_MIN_LENGTH = 20

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("dsn_credentials", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")),
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("jwt", re.compile(r"eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}")),
    ("aws_key", re.compile(r"(?:AKIA|ASIA)[0-9A-Z]{16}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("provider_key", re.compile(r"sk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("roottrace_key", re.compile(r"rt_(?:live|test)_[0-9a-f]{32}")),
    ("supabase_key", re.compile(r"sb_(?:secret|publishable)_[A-Za-z0-9_-]{12,}")),
)

_STRUCTURED = re.compile(r",|\\n|\\t")

#: A candidate "token" is a whitespace-free run of at least
#: `ENTROPY_MIN_LENGTH` characters. Unlike ingest's sanitiser — which walks
#: a structured payload one already-isolated field at a time — a rendered
#: prompt is one large block of source code and prose, so the entropy check
#: has to find its own candidates within it rather than being handed one.
#: Scanning the whole block as a single "value" (as a naive port of ingest's
#: function would) makes the check a near-total no-op on realistic
#: multi-line text, since real prompts are full of whitespace.
_TOKEN_CANDIDATE = re.compile(rf"\S{{{ENTROPY_MIN_LENGTH},}}")


def _marker(kind: str) -> str:
    return f"[REDACTED:{kind}]"


@dataclass(frozen=True, slots=True)
class RedactionHit:
    kind: str


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {character: value.count(character) for character in set(value)}
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def _redact_high_entropy_tokens(text: str) -> tuple[str, bool]:
    found = False

    def replace(match: re.Match[str]) -> str:
        nonlocal found
        candidate = match.group()
        # A value a named pattern already redacted is not re-examined —
        # without this, `postgresql://[REDACTED:dsn_credentials]@host/db`
        # is itself a whitespace-free high-entropy run and gets redacted a
        # second time, reporting a kind that says nothing about what was
        # actually found.
        if "[REDACTED:" in candidate:
            return candidate
        if _STRUCTURED.search(candidate):
            return candidate
        if shannon_entropy(candidate) > ENTROPY_THRESHOLD:
            found = True
            return _marker("high_entropy")
        return candidate

    return _TOKEN_CANDIDATE.sub(replace, text), found


def scan_and_redact(text: str) -> tuple[str, list[RedactionHit]]:
    """Redact `text`, reporting what was found — never what the value was,
    same rule as ingest's `Redaction.as_record`."""
    hits: list[RedactionHit] = []

    for kind, pattern in _PATTERNS:
        if pattern.search(text):
            text = pattern.sub(_marker(kind), text)
            hits.append(RedactionHit(kind=kind))

    text, found_entropy = _redact_high_entropy_tokens(text)
    if found_entropy:
        hits.append(RedactionHit(kind="high_entropy"))

    return text, hits
