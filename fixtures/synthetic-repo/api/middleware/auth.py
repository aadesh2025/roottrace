"""Request authentication.

No fixture bug lives here. It exists because a service of this shape has an
auth middleware, and retrieval has to learn to walk past files that are
plausible but irrelevant — a corpus where every file contains a bug would
teach the ranker the wrong thing.
"""

from __future__ import annotations

import logging

from models.user import User

logger = logging.getLogger(__name__)

ANONYMOUS_PATHS = ("/health", "/api/v2/webhooks/stripe")


class AuthMiddleware:
    def __init__(self, secret: str):
        self._secret = secret

    def is_anonymous_path(self, path: str) -> bool:
        return path.startswith(ANONYMOUS_PATHS)

    def authenticate(self, headers: dict[str, str]) -> User | None:
        token = headers.get("authorization", "")
        if not token.lower().startswith("bearer "):
            return None
        return User(id="u_9f2b1c", email="ada@example.com", plan="pro")
