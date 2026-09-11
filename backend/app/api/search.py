"""Semantic search API endpoints."""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.auth.rate_limit import enforce_rate_limit, search_limiter
from app.database import get_db
from app.models.chat import SearchDebugResponse, SearchRequest, SearchResponse

router = APIRouter(prefix="/api/search", tags=["search"])


async def _retrieve_for_request(
    request: SearchRequest, user_id: int, *, debug: bool
) -> Dict[str, Any]:
    """Shared retrieval call for /api/search and /api/search/debug.

    Delegates to the same hybrid + multi-query + rerank + expansion pipeline
    used by /api/chat, so all entry points have consistent recall. Maps the
    admission gate's timeout to 429 + Retry-After: the client should back off
    rather than silently receive a degraded (no-rerank/no-graph) result.
    """
    from app.services.query_gate import QueryGateTimeout
    from app.services.retriever import retrieve

    try:
        return await retrieve(
            request.query,
            user_id,
            top_k=request.top_k,
            use_graph_rag=request.use_graph_rag,
            conversation_history=None,
            debug=debug,
        )
    except QueryGateTimeout as e:
        raise HTTPException(
            status_code=429,
            detail=str(e),
            headers={"Retry-After": str(int(e.retry_after))},
        )


@router.post("", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    current_user: dict = Depends(get_current_user),
):
    """Semantic search across documents (unified hybrid retrieval)."""
    user_id = current_user["id"]
    # Throttle billable embedding/rerank work per user (no-op under test).
    enforce_rate_limit(search_limiter, f"search:{user_id}")

    context = await _retrieve_for_request(request, user_id, debug=False)
    return {
        "query": request.query,
        "chunks": context["chunks"],
        "entities": context["entities"],
        "relations": context["relations"],
    }


@router.post("/debug", response_model=SearchDebugResponse)
async def search_debug(
    request: SearchRequest,
    current_user: dict = Depends(get_current_user),
):
    """Run the retrieval pipeline with per-stage debug output (FEAT-024).

    Same pipeline, same admission gate, same rate limiter as POST /api/search
    — a debug call costs a full pipeline (embedding + rerank spend) every
    time because the retrieval cache is bypassed in both directions: the
    timings and stage snapshots must reflect a real, fresh run.
    """
    user_id = current_user["id"]
    enforce_rate_limit(search_limiter, f"search-debug:{user_id}")
    context = await _retrieve_for_request(request, user_id, debug=True)

    debug = context.get("debug") or {}
    return SearchDebugResponse(
        query=request.query,
        chunks=context["chunks"],
        entities=context["entities"],
        relations=context["relations"],
        debug=debug,
        titles=await _resolve_titles(debug, user_id),
    )


async def _resolve_titles(debug: Dict[str, Any], user_id: int) -> Dict[str, str]:
    """Map document_id → display title for every chunk referenced in the
    debug payload.

    The map is naturally partial: BM25-only hits carry no metadata, so they
    have no document_id — the UI falls back to the chunk_id for those.
    Single batched IN-list lookup, no N+1.
    """
    doc_ids: set = set()
    for channel in debug.get("channels", []):
        for hit in channel.get("hits", []):
            if hit.get("document_id"):
                doc_ids.add(hit["document_id"])
    for key in ("fused", "seeds", "expanded"):
        for entry in debug.get(key, []):
            if entry.get("document_id"):
                doc_ids.add(entry["document_id"])
    if not doc_ids:
        return {}

    placeholders = ",".join("?" * len(doc_ids))
    async with get_db() as db:
        async with db.execute(
            f"""SELECT id, title, original_filename FROM documents
                WHERE id IN ({placeholders}) AND user_id = ?""",
            (*doc_ids, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
    return {
        r["id"]: r["title"] or r["original_filename"] or r["id"]
        for r in rows
    }
