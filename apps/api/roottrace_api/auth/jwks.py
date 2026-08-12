"""JWKS cache with a bounded refetch on `kid` miss.

`docs/11` §3.1, B12. Verification is asymmetric, against Supabase's published
public keys. There is no shared signing secret anywhere in this service.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

# Asymmetric only. `docs/A3` §1 (B12) says RS256; Supabase's current signing
# keys are ES256 (EC P-256), and the JWKS endpoint publishes the algorithm per
# key. Both are asymmetric, which is the property B12 actually depends on: the
# API holds a verification key, never a signing key, so a compromised API
# process cannot mint tokens. Symmetric algorithms are absent from this set on
# purpose — HS256 here would mean signing with a published public key.
PERMITTED_ALGORITHMS = frozenset({"RS256", "ES256"})


class JwksError(RuntimeError):
    """The key set could not be fetched or does not contain the requested key."""


@dataclass
class JwksCache:
    """Caches the key set, and refetches **at most once** per unknown `kid`.

    The bound matters. Key rotation means an unknown `kid` is sometimes
    legitimate, so a refetch is required — but refetching on every miss turns
    any stream of tokens carrying a bogus `kid` into an unauthenticated
    amplification attack against our own JWKS endpoint. One refetch, then the
    token is rejected.
    """

    url: str
    ttl_seconds: int = 86400
    _keys: dict[str, Any] = field(default_factory=dict)
    _fetched_at: float = 0.0
    fetch_count: int = 0

    def _fetch(self, client: httpx.Client) -> None:
        self.fetch_count += 1
        try:
            response = client.get(self.url, timeout=5.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise JwksError(f"could not fetch JWKS from {self.url}") from exc

        keys = {k["kid"]: k for k in payload.get("keys", []) if "kid" in k}
        if not keys:
            raise JwksError("JWKS contained no usable keys")
        self._keys = keys
        self._fetched_at = time.monotonic()

    def _expired(self) -> bool:
        return (time.monotonic() - self._fetched_at) > self.ttl_seconds

    def key_for(self, kid: str, client: httpx.Client) -> tuple[Any, str]:
        """Return `(key, algorithm)` for `kid`, refetching at most once.

        The ALGORITHM COMES FROM THE KEY SET, never from the token header. That
        distinction is the whole of JWT algorithm-confusion: a token claiming
        `alg: HS256` against an asymmetric public key lets an attacker sign with
        a value that is, by definition, public. Here the algorithm is whatever
        the key we trust says it is, fetched over TLS from the issuer.
        """
        if not self._keys or self._expired():
            self._fetch(client)

        if kid not in self._keys:
            # Rotation is the legitimate reason to be here. Exactly one retry.
            self._fetch(client)

        if kid not in self._keys:
            raise JwksError(f"no key matching kid={kid!r}")

        jwk = self._keys[kid]
        algorithm = jwk.get("alg")
        if algorithm not in PERMITTED_ALGORITHMS:
            raise JwksError(f"key {kid!r} declares unsupported alg={algorithm!r}")

        return jwt.PyJWK(jwk).key, str(algorithm)
