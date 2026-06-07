"""Progress streaming API using Server-Sent Events (SSE).

Authentication:
    Header `Authorization: Bearer <jwt>` is preferred. EventSource cannot set
    custom headers, so for SSE connections we also accept the token via the
    `?token=` query string as a fallback. The query-string form leaks the token
    into reverse-proxy access logs and browser history — treat those URLs as
    sensitive and prefer short-lived tokens. A future fix is to switch to
    HttpOnly cookies set on login.
"""
import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.auth.jwt_handler import verify_token
from app.database import get_db
from app.services.progress_tracker import get_progress_emitter

logger = logging.getLogger(__name__)

router = APIRouter(tags=["progress"])


def _extract_bearer_token(authorization: Optional[str]) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1].strip()


def _resolve_token(
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
        logger.debug("SSE auth via query string (URL logged by reverse proxy)")
        return query_token.strip()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Missing token (need Authorization header or ?token= query string)",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _user_from_token(token: str) -> dict:
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
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
                )
            return dict(row)


async def _authenticate_sse(
    authorization: Optional[str], query_token: Optional[str]
) -> dict:
    token = _resolve_token(authorization, query_token)
    return await _user_from_token(token)


@router.get("/api/progress/{doc_id}")
async def stream_progress(
    doc_id: str,
    request: Request,
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None, alias="token"),
):
    """Stream progress updates for a document using SSE.

    Accepts the JWT via the `Authorization: Bearer <token>` header OR via
    the `?token=` query string (required for native EventSource clients
    which cannot set custom headers).
    """
    try:
        await _authenticate_sse(authorization, token)
    except HTTPException as exc:
        # Emit a parseable SSE error event so the client's onmessage sees
        # a typed event instead of an opaque network failure.
        return StreamingResponse(
            iter([f"data: {json.dumps({'type': 'error', 'error': exc.detail})}\n\n"]),
            media_type="text/event-stream",
            status_code=exc.status_code,
        )

    emitter = get_progress_emitter()
    queue = emitter.subscribe(doc_id)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30)
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'keepalive'})}\n\n"
                    continue
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") in ["complete", "error"]:
                    break
        except asyncio.CancelledError:
            pass
        finally:
            emitter.unsubscribe(doc_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/progress/{doc_id}/history")
async def get_progress_history(
    doc_id: str,
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(default=None, alias="token"),
):
    """Get progress history for a document. Accepts Authorization header or ?token=."""
    try:
        current_user = await _authenticate_sse(authorization, token)
    except HTTPException as exc:
        return {"error": exc.detail, "history": []}

    emitter = get_progress_emitter()
    history = await emitter.get_history(doc_id, current_user["id"])
    return {"history": history}
