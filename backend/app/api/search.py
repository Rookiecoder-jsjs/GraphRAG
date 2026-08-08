"""Semantic search API endpoints."""
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.auth.rate_limit import enforce_rate_limit, search_limiter
from app.models.chat import SearchRequest, SearchResponse
from app.services.embedding import get_embedding_service
from app.services.chroma_client import get_chroma_client
from app.services.neo4j_client import get_neo4j_client
from app.services.reranker import get_rerank_service

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
    from app.services.retriever import retrieve
    context = await retrieve(
        request.query,
        user_id,
        top_k=request.top_k,
        use_graph_rag=getattr(request, "use_graph_rag", False),
        conversation_history=None,
    )

    return {
        "query": request.query,
        "chunks": context["chunks"],
        "entities": context["entities"],
        "relations": context["relations"],
    }
