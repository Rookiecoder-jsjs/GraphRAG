"""Semantic search API endpoints."""
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
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

    # Expand context if requested
    if request.include_context and chunks:
        expanded_chunks = []
        for chunk in chunks:
            expanded_chunks.append(chunk)
            # Get context chunks
            context = chroma.get_chunk_context(
                chunk["chunk_id"], user_id, window_size=1
            )
            expanded_chunks.extend(context)
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
