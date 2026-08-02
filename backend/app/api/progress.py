"""Progress streaming API using Server-Sent Events (SSE).

Authentication:
    Header `Authorization: Bearer <jwt>` is preferred. EventSource cannot set
    custom headers, so for SSE connections we also accept the token via the
    `?token=` query string as a fallback — see app/api/_token_auth.py, the
    shared helper this endpoint uses together with the image-serving route.
"""
import asyncio
import json
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.api._token_auth import (
    authenticate_with_token_fallback as _authenticate_sse,
)
from app.database import get_db
from app.services.progress_tracker import get_progress_emitter

router = APIRouter(tags=["progress"])


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
            # Remove ONLY this subscriber's queue so other watchers survive.
            emitter.unsubscribe(doc_id, queue)

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

    # SECURITY: 404 for a document the caller doesn't own, rather than
    # silently returning an empty history.
    if not await _verify_doc_owner(doc_id, current_user["id"]):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )

    emitter = get_progress_emitter()
    history = await emitter.get_history(doc_id, current_user["id"])
    return {"history": history}
