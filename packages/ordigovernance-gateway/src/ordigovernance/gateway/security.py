"""Bearer-token authentication (V1: a single execute-scope token)."""

from __future__ import annotations

import hmac
from typing import Awaitable, Callable

from fastapi import Header, HTTPException


def build_auth_dependency(token: str) -> Callable[..., Awaitable[None]]:
    """Build the FastAPI dependency enforcing the bearer token."""

    async def require_token(
            authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {token}"
        if (not token or not authorization
                or not hmac.compare_digest(authorization, expected)):
            raise HTTPException(
                status_code=401,
                detail="missing or invalid bearer token",
            )

    return require_token