"""Semantic search API endpoints."""
from typing import Dict, Any
from fastapi import APIRouter, Depends

from app.api.auth import get_current_user
from app.config import get_settings
from app.models.chat import SearchRequest, SearchResponse
from app.services.retrieval import (
    RetrievalContext,
    build_pipeline,
    parse_pipeline,
    run_pipeline,
)

router = APIRouter(prefix="/api/search", tags=["search"])


@router.post("", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    current_user: dict = Depends(get_current_user)
):
    """Semantic search across documents.

    Runs the search retrieval pipeline (settings.SEARCH_PIPELINE):
    vector-only by design — no query rewrite, no BM25 — followed by
    cross-modal image promotion, context expansion, and graph entity
    enrichment.
    """
    settings = get_settings()
    ctx = RetrievalContext(
        query=request.query,
        user_id=current_user["id"],
        settings=settings,
        top_k=request.top_k,
        include_context=request.include_context,
        # Search is vector-only: no rewrite step, no BM25 lane.
        use_hybrid=False,
        use_query_rewrite=False,
        # Recall: the endpoint historically fetched top_k * 4 before reranking.
        vector_recall=request.top_k * 4,
        bm25_recall=0,
        # Entity enrichment knobs (search): top-5 names, depth 1, no
        # "Related" appends; context neighbors deduped two-phase.
        entity_name_limit=5,
        entity_depth=1,
        append_related=False,
        dedup_context=True,
    )
    ctx = await run_pipeline(
        build_pipeline(parse_pipeline(settings.SEARCH_PIPELINE)), ctx
    )

    return {
        "query": request.query,
        "chunks": ctx.chunks,
        "entities": ctx.entities,
        "relations": ctx.relations,
    }
