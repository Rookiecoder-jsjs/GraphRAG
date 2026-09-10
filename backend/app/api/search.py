"""Semantic search API endpoints."""
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.auth.rate_limit import enforce_rate_limit, search_limiter
from app.models.chat import SearchRequest, SearchResponse

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    current_user: dict = Depends(get_current_user)
):
    """Semantic search across documents (unified hybrid retrieval)."""
    user_id = current_user["id"]
    # Throttle billable embedding/rerank work per user (no-op under test).
    enforce_rate_limit(search_limiter, f"search:{user_id}")

    # Delegate to the same hybrid + multi-query + rerank + expansion pipeline
    # used by /api/chat, so the two entry points have consistent recall.
    from app.services.query_gate import QueryGateTimeout
    from app.services.retriever import retrieve
    try:
        context = await retrieve(
            request.query,
            user_id,
            top_k=request.top_k,
            use_graph_rag=getattr(request, "use_graph_rag", False),
            conversation_history=None,
        )
    except QueryGateTimeout as e:
        # Admission control (ADR-009): the retrieval queue was full for the
        # whole queue budget — tell the client to back off instead of
        # silently serving a degraded (no-rerank/no-graph) result.
        raise HTTPException(
            status_code=429,
            detail=str(e),
            headers={"Retry-After": str(int(e.retry_after))},
        )

    return {
        "query": request.query,
        "chunks": context["chunks"],
        "entities": context["entities"],
        "relations": context["relations"],
    }
