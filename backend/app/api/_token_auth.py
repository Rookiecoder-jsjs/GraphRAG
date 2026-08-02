"""Shared token authentication for endpoints that can't require headers.

Browser-native constructs (EventSource, <img>) cannot set an Authorization
header, so SSE streams and the image-serving endpoint accept the JWT via a
`?token=` query string as a fallback. This module centralizes that logic —
progress.py and the documents image endpoint both use it, so the auth rules
cannot drift between the two surfaces.

The query-string form leaks the token into reverse-proxy access logs and
browser history — treat those URLs as sensitive and prefer short-lived
tokens. A future fix is HttpOnly cookies set on login.
"""
import logging
from typing import Optional

from fastapi import HTTPException, status

from app.auth.jwt_handler import verify_token
from app.database import get_db

logger = logging.getLogger(__name__)


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1].strip()


def resolve_token(
    authorization: Optional[str], query_token: Optional[str]
) -> str:
    """Pick the token: prefer the Authorization header, fall back to ?token="""
    if authorization:
        try:
            return _extract_bearer_token(authorization)
        except HTTPException:
            if not query_token:
                raise
    if query_token:
        logger.debug("Auth via query string (URL logged by reverse proxy)")
        return query_token.strip()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing token (need Authorization header or ?token= query string)",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def authenticate_with_token_fallback(
    authorization: Optional[str], query_token: Optional[str]
) -> dict:
    """Resolve + verify the token and return the user row as a dict.

    Raises HTTPException(401) when the token is missing, invalid, or the
    user no longer exists.
    """
    token = resolve_token(authorization, query_token)
    payload = verify_token(token)
    username = payload.get("sub")
    async with get_db() as db:
        async with db.execute(
            "SELECT id, username, created_at FROM users WHERE username = ?",
            (username,),
        ) as cursor:
            row = await cursor.fetchone()
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="User not found",
                )
            return dict(row)
