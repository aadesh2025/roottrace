"""FastAPI auth dependencies.

`docs/05` §2.2. One dependency, used by every authenticated route, so there is
exactly one place a token becomes a user.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

import httpx
from fastapi import Depends, Header, HTTPException, status

from roottrace_api.auth.jwks import JwksCache
from roottrace_api.auth.verify import AuthenticatedUser, AuthError, verify_access_token
from roottrace_api.settings import Settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Boot invariants run here, once, on first access."""
    return Settings()  # type: ignore[call-arg]


@lru_cache(maxsize=1)
def get_jwks(url: str, ttl_seconds: int) -> JwksCache:
    return JwksCache(url=url, ttl_seconds=ttl_seconds)


@lru_cache(maxsize=1)
def get_http_client() -> httpx.Client:
    return httpx.Client(timeout=5.0)


def current_user(
    authorization: Annotated[str | None, Header()] = None,
    settings: Annotated[Settings, Depends(get_settings)] = None,  # type: ignore[assignment]
) -> AuthenticatedUser:
    """Resolve the caller, or 401.

    The error body never echoes the token or any claim: a 401 that quotes what
    it received is a log-injection and token-leak surface, and tells an attacker
    which part of their forgery was wrong.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "RT-AUTH-0001", "message": "missing bearer token"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Not an assert: `python -O` strips those, and this is the guard that keeps
    # us from constructing a JWKS cache with a None URL. The boot invariant
    # already makes it unreachable in `api`; this is what keeps it unreachable
    # if that invariant is ever loosened.
    if settings.supabase_jwks_url is None:  # pragma: no cover — boot invariant
        raise RuntimeError("api booted without RT_SUPABASE_JWKS_URL")
    jwks = get_jwks(str(settings.supabase_jwks_url), settings.supabase_jwks_cache_ttl_seconds)

    try:
        return verify_access_token(
            authorization.split(" ", 1)[1].strip(),
            jwks=jwks,
            client=get_http_client(),
        )
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "RT-AUTH-0002", "message": "invalid token"},
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


CurrentUser = Annotated[AuthenticatedUser, Depends(current_user)]
