"""API key resolution (`05` §2.1, `11` §3).

An ingest key identifies a **project**, not a person. It carries one scope,
`events:write`, and cannot read anything — which is the most important
authentication decision in the product: these keys live in customer application
config, get committed to repositories and appear in CI logs, so they leak. A
leaked one must not expose a single line of source, investigation or setting.

Stored as `sha256(key)`. The plaintext is returned once at creation and is
unrecoverable thereafter, so a database dump does not yield usable keys.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Any

#: `05` §2.1: `rt_{live|test}_{32 hex}`. Checked before any database work — a
#: malformed key is rejected on shape, so a flood of garbage costs a regex
#: rather than a query.
KEY_PATTERN = re.compile(r"^rt_(live|test)_[0-9a-f]{32}$")

CACHE_TTL_SECONDS = 60

REQUIRED_SCOPE = "events:write"


@dataclass(frozen=True, slots=True)
class ResolvedKey:
    key_id: str
    project_id: str
    scopes: tuple[str, ...]

    @property
    def may_write_events(self) -> bool:
        return REQUIRED_SCOPE in self.scopes


class InvalidKeyError(Exception):
    """The credential is missing, malformed, unknown or revoked.

    One exception for all four on purpose. Distinguishing "unknown key" from
    "revoked key" in the response would let an attacker enumerate which keys
    ever existed, and there is nothing a legitimate client does differently in
    the two cases.
    """


def hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def extract_bearer(authorization: str | None) -> str:
    """Pull the key out of an `Authorization` header, or raise."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise InvalidKeyError("missing bearer credential")

    candidate = authorization.split(" ", 1)[1].strip()
    if not KEY_PATTERN.match(candidate):
        raise InvalidKeyError("malformed api key")
    return candidate


def matches(stored_hash: str, candidate_hash: str) -> bool:
    """Constant-time compare (`11` §3).

    Both sides are already hashes, so the timing signal is small — but the
    comparison is free to make constant-time and the habit is what matters:
    the next comparison someone writes here may be against a secret.
    """
    return hmac.compare_digest(stored_hash, candidate_hash)


def cache_key(key_hash: str) -> str:
    """Namespaced by hash, never by plaintext.

    Redis keys turn up in `MONITOR`, in slow-log output and in memory dumps. A
    cache keyed by the plaintext would put a live credential in all three.
    """
    return f"rt:apikey:{key_hash}"


def revocation_cache_key(key_id: str) -> str:
    """`11` §3: revocation is immediate and actively purges the cache entry,
    rather than waiting out the 60-second TTL."""
    return f"rt:apikey:id:{key_id}"


def serialise(resolved: ResolvedKey) -> dict[str, Any]:
    return {
        "key_id": resolved.key_id,
        "project_id": resolved.project_id,
        "scopes": list(resolved.scopes),
    }


def deserialise(payload: dict[str, Any]) -> ResolvedKey:
    return ResolvedKey(
        key_id=str(payload["key_id"]),
        project_id=str(payload["project_id"]),
        scopes=tuple(payload.get("scopes", ())),
    )
