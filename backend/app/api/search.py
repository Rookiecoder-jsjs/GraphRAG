"""Semantic search API endpoints."""
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.config import get_settings
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
    """Semantic search across documents."""
    user_id = current_user["id"]

    # Generate query embedding
    embedding_service = await get_embedding_service()
    query_embedding = await embedding_service.embed_single(request.query)

    # Search ChromaDB with larger recall
    chroma = get_chroma_client()
    chunks = chroma.search(query_embedding, user_id, top_k=request.top_k * 4)

    # Rerank to get top_k most relevant
    rerank_service = await get_rerank_service()
    chunks = await rerank_service.rerank(request.query, chunks, top_k=request.top_k)

    # Cross-modal promotion, two tiers:
    #   sim >= IMAGE_PROMOTION_THRESHOLD — the multimodal embedding
    #   model itself says the image is semantically close to the query
    #   (it shares the vector space with the query). The text-only
    #   reranker cannot see images, and its scores live on a compressed
    #   ~0.00x scale, so an unrelated text can outrank a genuinely
    #   relevant image. Such images are promoted AHEAD of the text
    #   block, best (highest sim) first.
    #   sim < threshold — no proven relevance; keep the image inside
    #   the top_k window at the last visible slot instead of burying
    #   it past the cutoff (text-first ordering preserved).
    images = [
        c for c in chunks
        if (c.get("metadata") or {}).get("modality") == "image"
    ]
    if images and not any(
        (c.get("metadata") or {}).get("modality") == "image"
        for c in chunks[: request.top_k]
    ):
        def _sim(chunk: Dict[str, Any]) -> float:
            dist = chunk.get("distance")
            return 1.0 - dist if isinstance(dist, (int, float)) else 0.0

        hot = sorted(
            (c for c in images if _sim(c) >= get_settings().IMAGE_PROMOTION_THRESHOLD),
            key=_sim, reverse=True,
        )
        cold = [c for c in images if _sim(c) < get_settings().IMAGE_PROMOTION_THRESHOLD]
        if hot:
            chunks = hot + chunks
        if cold:
            # len(chunks) - len(images) == len(texts); with a text-heavy
            # window this equals top_k - 1 (insert at the last visible slot).
            insert_at = min(request.top_k - 1, max(0, len(chunks) - len(images)))
            chunks = chunks[:insert_at] + cold[:1] + chunks[insert_at:]

    # Expand context if requested. Adjacent markdown sections share
    # prev/next pointers, so a naive append would duplicate the same
    # chunk several times in the response — visible to the user as
    # identical cards at different ranks. Two-phase, order-preserving
    # dedup: the reranked list goes in FIRST (it carries the
    # relevance_score), then only context neighbours that are NOT
    # already present are appended. A single-pass loop would let a
    # scoreless context copy of a reranked chunk win the seen-check
    # and silently drop the reranked version's relevance_score.
    if request.include_context and chunks:
        expanded_chunks: List[Dict[str, Any]] = []
        seen_chunk_ids: set[str] = set()
        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            if chunk_id not in seen_chunk_ids:
                seen_chunk_ids.add(chunk_id)
                expanded_chunks.append(chunk)
        for chunk in chunks:
            for ctx in chroma.get_chunk_context(
                chunk["chunk_id"], user_id, window_size=1
            ):
                ctx_id = ctx["chunk_id"]
                if ctx_id not in seen_chunk_ids:
                    seen_chunk_ids.add(ctx_id)
                    expanded_chunks.append(ctx)
        chunks = expanded_chunks

    # Get entities from chunks
    neo4j = await get_neo4j_client()
    chunk_ids = [c["chunk_id"] for c in chunks]
    entities = await neo4j.get_entities_from_chunks(chunk_ids, user_id)

    # Get relations between entities
    entity_names = [e["name"] for e in entities]
    relations = []
    if len(entity_names) >= 2:
        graph_data = await neo4j.get_related_entities(entity_names[:5], user_id, depth=1)
        relations = graph_data.get("relations", [])

    return {
        "query": request.query,
        "chunks": chunks,
        "entities": entities,
        "relations": relations
    }
