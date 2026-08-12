"""Access-token verification against Supabase's JWKS.

`docs/05` §2.2, `docs/11` §3.1. The claim set is Supabase GoTrue's, and both
login paths — GitHub OAuth and magic link — issue the same token with the same
`sub`, so `rt_auth.uid()` and every RLS policy behave identically. There is no
dev-mode bypass (`A3` §5.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from roottrace_api.auth.jwks import JwksCache, JwksError


class AuthError(Exception):
    """Verification failed. Never carries the token or any claim value."""


@dataclass(frozen=True)
class AuthenticatedUser:
    user_id: str
    email: str | None
    role: str


def verify_access_token(
    token: str,
    *,
    jwks: JwksCache,
    client: httpx.Client,
    audience: str = "authenticated",
) -> AuthenticatedUser:
    """Verify an access token and return its subject.

    The signing algorithm is not assumed. Supabase GoTrue issues ES256 today
    and has issued RS256 in other configurations; this function trusts
    neither claim and instead uses whatever asymmetric algorithm the matching
    JWKS entry declares (see below).

    The permitted algorithm is taken from the JWKS entry, not from the token's
    own `alg` header. Trusting the header is how `alg: none` and HS256-confusion
    attacks work — under HS256 the "secret" would be the published public key.
    `JwksCache` additionally restricts the key set to asymmetric algorithms.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError("malformed token") from exc

    kid = header.get("kid")
    if not kid:
        raise AuthError("token has no kid")

    try:
        key, algorithm = jwks.key_for(kid, client)
    except JwksError as exc:
        raise AuthError("no verification key for this token") from exc

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key=key,
            algorithms=[algorithm],
            audience=audience,
            options={"require": ["exp", "sub"], "verify_exp": True},
        )
    except jwt.PyJWTError as exc:
        raise AuthError("token failed verification") from exc

    subject = claims.get("sub")
    if not subject:
        raise AuthError("token has no subject")

    return AuthenticatedUser(
        user_id=str(subject),
        email=claims.get("email"),
        role=str(claims.get("role", "authenticated")),
    )
