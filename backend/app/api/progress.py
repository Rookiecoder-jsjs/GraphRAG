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
import time
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.auth.jwt_handler import verify_token
from app.config import get_settings
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


def _sse_error(detail: str, status_code: int) -> StreamingResponse:
    """Return a parseable SSE error event so clients see a typed event."""
    return StreamingResponse(
        iter([f"data: {json.dumps({'type': 'error', 'error': detail})}\n\n"]),
        media_type="text/event-stream",
        status_code=status_code,
    )


async def _verify_doc_owner(doc_id: str, user_id: int) -> bool:
    """Confirm a document belongs to the user.

    SECURITY: without this, any authenticated user could subscribe to another
    user's ``doc_id`` and eavesdrop on their processing events (document
    titles, extracted entity names, error text).
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM documents WHERE id = ? AND user_id = ?",
            (doc_id, user_id),
        ) as cursor:
            return await cursor.fetchone() is not None


# SSE idle window before a keepalive frame is sent (an otherwise-silent
# connection can be timed out by proxies / the client EventSource).
_KEEPALIVE_SECONDS = 30


def _event_from_row(row: dict) -> dict:
    """Reconstruct the original SSE event dict from a progress_history row.

    Rows written since the payload column existed carry the exact ``data``
    dict ``emit_and_save`` received; legacy rows (NULL payload) degrade to
    the columns we still have.
    """
    data = None
    payload = row.get("payload_json")
    if payload:
        try:
            data = json.loads(payload)
        except (ValueError, TypeError):
            data = None
    if data is None:
        stage = row.get("stage")
        data = {"percent": row.get("percent", 0)}
        if stage in ("complete", "error"):
            data["stage"] = stage
            if row.get("error_message"):
                data["error"] = row["error_message"]
    return {"type": row.get("stage"), "message": row.get("message"), "data": data}


def _terminal_type(event: dict) -> bool:
    return event.get("type") in ("complete", "error")


def _frame(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _progress_event_stream(
    emitter,
    doc_id: str,
    user_id: int,
    *,
    poll_seconds: float,
    is_disconnected=None,
):
    """Yield SSE data frames for a doc's progress, replay then tail.

    Events are read from SQLite by polling — not an in-process bus — so the
    stream works even when the pipeline runs in a different worker, and a
    reconnect replays what already happened before tailing new rows.

    ``is_disconnected`` is an optional async callable (``request.is_disconnected``)
    checked each poll; tests omit it to stream indefinitely.
    """
    last_id = 0
    last_sent = time.monotonic()
    try:
        # Replay the whole lifecycle first (reconnect / opened late), so the
        # client converges with history before we tail new rows.
        for row in await emitter.get_rows_since(doc_id, user_id, after_id=0):
            last_id = row["id"]
            last_sent = time.monotonic()
            event = _event_from_row(row)
            yield _frame(event)
            if _terminal_type(event):
                return
        # Tail: poll for rows newer than the last one we delivered.
        while True:
            if is_disconnected is not None and await is_disconnected():
                break
            rows = await emitter.get_rows_since(doc_id, user_id, after_id=last_id)
            if rows:
                for row in rows:
                    last_id = row["id"]
                    last_sent = time.monotonic()
                    event = _event_from_row(row)
                    yield _frame(event)
                    if _terminal_type(event):
                        return
            elif time.monotonic() - last_sent >= _KEEPALIVE_SECONDS:
                yield _frame({"type": "keepalive"})
                last_sent = time.monotonic()
            await asyncio.sleep(poll_seconds)
    except asyncio.CancelledError:
        pass


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
        current_user = await _authenticate_sse(authorization, token)
    except HTTPException as exc:
        # Emit a parseable SSE error event so the client's onmessage sees
        # a typed event instead of an opaque network failure.
        return _sse_error(exc.detail, exc.status_code)

    # SECURITY: only the owner may stream a document's progress.
    if not await _verify_doc_owner(doc_id, current_user["id"]):
        return _sse_error("Document not found", status.HTTP_404_NOT_FOUND)

    emitter = get_progress_emitter()
    user_id = current_user["id"]
    poll_seconds = max(get_settings().PROGRESS_POLL_SECONDS, 0.1)

    return StreamingResponse(
        _progress_event_stream(
            emitter, doc_id, user_id,
            poll_seconds=poll_seconds,
            is_disconnected=request.is_disconnected,
        ),
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

    # SECURITY: 404 for a document the caller doesn't own, rather than
    # silently returning an empty history.
    if not await _verify_doc_owner(doc_id, current_user["id"]):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    emitter = get_progress_emitter()
    history = await emitter.get_history(doc_id, current_user["id"])
    return {"history": history}
