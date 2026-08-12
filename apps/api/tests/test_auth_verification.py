"""Asymmetric token verification and the bounded JWKS refetch (T1.4, B12).

Both tests here exist because the obvious version of each would pass while
proving nothing:

  * "a bad token is rejected" passes if the token is merely malformed. So the
    wrong-key test signs a *structurally perfect* token with a genuinely
    different RSA key — the only thing wrong with it is the signature.
  * "a kid miss eventually verifies" passes with an unbounded refetch loop,
    which is an unauthenticated amplification attack on our own JWKS endpoint.
    So the count is asserted exactly, not merely as "more than zero".
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from roottrace_api.auth.jwks import JwksCache, JwksError
from roottrace_api.auth.verify import AuthError, verify_access_token

pytestmark = pytest.mark.unit


def _keypair() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(key: Any, kid: str) -> dict[str, Any]:
    from jwt.algorithms import RSAAlgorithm

    jwk: dict[str, Any] = dict(RSAAlgorithm.to_jwk(key.public_key(), as_dict=True))
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return jwk


def _token(key: Any, kid: str, **overrides: Any) -> str:
    claims = {
        "sub": "aaaaaaaa-0000-4000-8000-000000000001",
        "aud": "authenticated",
        "role": "authenticated",
        "email": "a@example.test",
        "exp": int(time.time()) + 3600,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})


class _StubTransport(httpx.BaseTransport):
    """Serves a JWKS document and counts how often it is asked for one."""

    def __init__(self, keys: list[dict[str, Any]]) -> None:
        self.keys = keys
        self.requests = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return httpx.Response(200, json={"keys": self.keys}, request=request)


@pytest.fixture
def signing_key() -> Any:
    return _keypair()


@pytest.fixture
def transport(signing_key: Any) -> _StubTransport:
    return _StubTransport([_jwk(signing_key, "kid-1")])


@pytest.fixture
def client(transport: _StubTransport) -> httpx.Client:
    return httpx.Client(transport=transport)


@pytest.fixture
def jwks() -> JwksCache:
    return JwksCache(url="https://supabase.test/auth/v1/.well-known/jwks.json")


def test_valid_token_is_accepted(signing_key: Any, jwks: JwksCache, client: httpx.Client) -> None:
    user = verify_access_token(_token(signing_key, "kid-1"), jwks=jwks, client=client)
    assert user.user_id == "aaaaaaaa-0000-4000-8000-000000000001"
    assert user.role == "authenticated"


def test_token_signed_with_the_wrong_key_is_rejected(jwks: JwksCache, client: httpx.Client) -> None:
    """The token is well-formed, unexpired, correctly audienced, and carries a
    `kid` the key set knows. The signature is the only thing wrong with it."""
    attacker_key = _keypair()
    forged = _token(attacker_key, "kid-1")

    # Proof the token is otherwise perfect: it parses and its claims are right.
    unverified = jwt.decode(forged, options={"verify_signature": False})
    assert unverified["sub"] == "aaaaaaaa-0000-4000-8000-000000000001"
    assert unverified["aud"] == "authenticated"

    with pytest.raises(AuthError):
        verify_access_token(forged, jwks=jwks, client=client)


def test_kid_miss_triggers_exactly_one_refetch(
    jwks: JwksCache, client: httpx.Client, transport: _StubTransport
) -> None:
    """Rotation makes an unknown `kid` legitimate, so one refetch is required.

    Refetching per miss would let anyone with a bogus `kid` drive unbounded
    outbound requests to our JWKS endpoint — a DoS we would be executing on
    ourselves, from unauthenticated input.
    """
    other_key = _keypair()
    with pytest.raises(AuthError):
        verify_access_token(_token(other_key, "kid-unknown"), jwks=jwks, client=client)

    # One to populate the cache, one for the miss. Not three, not per-attempt.
    assert transport.requests == 2, (
        f"expected exactly 2 JWKS fetches (initial + one retry), got {transport.requests}"
    )
    assert jwks.fetch_count == 2


def test_repeated_unknown_kids_do_not_amplify(
    jwks: JwksCache, client: httpx.Client, transport: _StubTransport
) -> None:
    """Ten bogus tokens must not produce twenty fetches beyond the bound."""
    other_key = _keypair()
    for index in range(10):
        with pytest.raises(AuthError):
            verify_access_token(_token(other_key, f"bogus-{index}"), jwks=jwks, client=client)

    # 1 initial + 1 per miss. The point is that it is LINEAR and bounded per
    # call, never a loop inside one call.
    assert transport.requests == 11


def test_expired_token_is_rejected(signing_key: Any, jwks: JwksCache, client: httpx.Client) -> None:
    expired = _token(signing_key, "kid-1", exp=int(time.time()) - 60)
    with pytest.raises(AuthError):
        verify_access_token(expired, jwks=jwks, client=client)


def test_token_without_kid_is_rejected(
    signing_key: Any, jwks: JwksCache, client: httpx.Client
) -> None:
    headerless = jwt.encode(
        {"sub": "x", "aud": "authenticated", "exp": int(time.time()) + 60},
        signing_key,
        algorithm="RS256",
    )
    with pytest.raises(AuthError):
        verify_access_token(headerless, jwks=jwks, client=client)


def test_algorithm_comes_from_the_key_set_not_the_token(
    signing_key: Any, jwks: JwksCache, client: httpx.Client
) -> None:
    """Algorithm confusion: an HS256 token HMAC'd with the RSA PUBLIC key.

    If the verifier honoured the token's own `alg`, this would verify — the
    "shared secret" is a value we publish in the JWKS. It must be rejected
    because the key set says the key is RS256.

    Forged by hand: PyJWT refuses to *encode* HS256 with an asymmetric key, so
    building it through the library would test the library's hygiene rather than
    our verifier's.
    """
    import base64
    import hashlib
    import hmac
    import json

    from cryptography.hazmat.primitives import serialization

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    public_pem = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    header = b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": "kid-1"}).encode())
    payload = b64(
        json.dumps(
            {"sub": "attacker", "aud": "authenticated", "exp": int(time.time()) + 3600}
        ).encode()
    )
    signing_input = header + b"." + payload
    signature = b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
    confused = (signing_input + b"." + signature).decode()

    with pytest.raises(AuthError):
        verify_access_token(confused, jwks=jwks, client=client)


def test_symmetric_keys_are_refused_by_the_cache(jwks: JwksCache) -> None:
    """Even if an issuer published one, we would not use it."""
    symmetric = _StubTransport([{"kid": "kid-hs", "kty": "oct", "alg": "HS256", "k": "c2VjcmV0"}])
    with pytest.raises(JwksError, match="unsupported alg"):
        jwks.key_for("kid-hs", httpx.Client(transport=symmetric))


def test_empty_key_set_is_an_error_not_an_accept(jwks: JwksCache) -> None:
    """A JWKS endpoint returning `{}` must fail closed."""
    empty = _StubTransport([])
    with pytest.raises(JwksError):
        jwks.key_for("kid-1", httpx.Client(transport=empty))
