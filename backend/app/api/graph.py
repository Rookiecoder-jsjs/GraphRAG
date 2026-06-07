"""Knowledge graph API endpoints."""
import logging
from typing import List, Dict, Any
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Path, status

from app.api.auth import get_current_user
from app.database import get_db
from app.models.graph import (
    GraphQuery, GraphQueryResponse, EntityResponse,
    RelationResponse, GraphVisualization, GraphNode, GraphEdge,
    UpdateEntityRequest, MergeEntityRequest,
)
from app.services.neo4j_client import get_neo4j_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/graph", tags=["graph"])


@router.get("/entities", response_model=List[EntityResponse])
async def list_entities(
    query: str = "",
    current_user: dict = Depends(get_current_user)
):
    """List entities matching query."""
    user_id = current_user["id"]
    logger.info("List entities: query=%r, user_id=%d", query, user_id)
    neo4j = await get_neo4j_client()
    entities = await neo4j.search_entities(query, user_id, limit=50)
    logger.info("Returning %d entities", len(entities))
    return entities


@router.post("/query", response_model=GraphQueryResponse)
async def query_graph(
    request: GraphQuery,
    current_user: dict = Depends(get_current_user)
):
    """Query graph by semantic search - finds entities related to query concept."""
    user_id = current_user["id"]
    logger.info("Semantic query: %r by user_id=%d", request.query, user_id)

    # Step 1: Semantic search - find related chunks using vector similarity
    from app.services.embedding import get_embedding_service
    from app.services.chroma_client import get_chroma_client

    embedding_service = await get_embedding_service()
    query_embedding = await embedding_service.embed_single(request.query)

    chroma = get_chroma_client()
    related_chunks = chroma.search(query_embedding, user_id, top_k=10)
    logger.info("Found %d semantically related chunks", len(related_chunks))

    neo4j = await get_neo4j_client()

    # Step 2: Get entities mentioned in these chunks
    chunk_ids = [c["chunk_id"] for c in related_chunks]
    entities = []
    if chunk_ids:
        entities = await neo4j.get_entities_from_chunks(chunk_ids, user_id)
        logger.info("Found %d entities from related chunks", len(entities))

    # If no entities found from semantic search, fall back to name search
    if not entities:
        logger.info("Falling back to name-based search")
        entities = await neo4j.search_entities(request.query, user_id, limit=5)
        logger.info("Found %d entities by name", len(entities))
    if not entities:
        return {
            "center_nodes": [],
            "related_nodes": [],
            "relations": [],
            "visualization": {"nodes": [], "edges": []}
        }

    # Get related entities with their connections
    entity_names = [e["name"] for e in entities]
    logger.info("Expanding graph for entities: %s", entity_names[:5])
    graph_data = await neo4j.get_related_entities(
        entity_names[:10], user_id, depth=request.depth  # Limit to top 10 entities
    )
    logger.info(
        "Found %d center nodes, %d related nodes, %d relations",
        len(graph_data["center_nodes"]),
        len(graph_data["related_nodes"]),
        len(graph_data["relations"]),
    )

    # Build visualization data
    nodes = []
    edges = []
    node_ids = set()

    for entity in graph_data["center_nodes"]:
        node_id = entity["name"]
        if node_id not in node_ids:
            nodes.append({
                "id": node_id,
                "type": "Entity",
                "label": entity["name"],
                # 真实实体类型在顶层 — 前端 categorize.js 才能正确归类
                "entity_type": entity["type"],
                "description": entity.get("description", ""),
                "is_center": True,
                "is_highlighted": True,
                "properties": {"type": entity["type"], "is_center": True}
            })
            node_ids.add(node_id)

    for entity in graph_data["related_nodes"]:
        node_id = entity["name"]
        if node_id not in node_ids:
            nodes.append({
                "id": node_id,
                "type": "Entity",
                "label": entity["name"],
                "entity_type": entity["type"],
                "description": entity.get("description", ""),
                "is_center": False,
                "is_highlighted": True,
                "properties": {"type": entity["type"], "is_center": False}
            })
            node_ids.add(node_id)

    for relation in graph_data["relations"]:
        edges.append({
            "id": f"{relation['source']}-{relation['relation_type']}-{relation['target']}",
            "source": relation["source"],
            "target": relation["target"],
            "label": relation["relation_type"],
            "type": "RELATES_TO"
        })

    return {
        "center_nodes": graph_data["center_nodes"],
        "related_nodes": graph_data["related_nodes"],
        "relations": graph_data["relations"],
        "visualization": {"nodes": nodes, "edges": edges}
    }


@router.get("/visualization")
async def get_visualization(
    query: str = "",
    current_user: dict = Depends(get_current_user)
):
    """Get full graph visualization data for the user."""
    user_id = current_user["id"]
    logger.info("Getting full visualization for user_id=%d", user_id)

    neo4j = await get_neo4j_client()

    # Get all entities and relations for this user
    viz_data = await neo4j.get_full_graph_for_visualization(user_id)
    logger.info("Returning %d nodes and %d edges", len(viz_data["nodes"]), len(viz_data["edges"]))

    return viz_data


# ---------- Entity curation (PATCH / DELETE / merge) ---------------------
#
# These power the "click a node, edit / merge / delete" UI. All three
# operations are destructive (or near-destruction) and are guarded by the
# (entity_name, user_id) ownership tuple at the Neo4j layer — there is
# no way to touch another user's data even if a malicious client passes
# someone else's entity name.
#
# The entity name is a path parameter, but entity names can contain almost
# any character (we don't restrict at the LLM extraction layer), so we
# accept it as `str` and URL-decode it before querying. FastAPI's
# path matching stops at `/`, so the frontend must `encodeURIComponent`
# names that contain slashes (rare, but possible).

@router.patch("/entities/{entity_name:path}")
async def update_entity(
    payload: UpdateEntityRequest,
    entity_name: str = Path(..., description="URL-encoded entity name"),
    current_user: dict = Depends(get_current_user),
):
    """Edit an entity's type and/or description. Returns the updated row."""
    user_id = current_user["id"]
    name = unquote(entity_name).strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="entity_name is required",
        )
    if payload.entity_type is None and payload.description is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one of entity_type / description must be provided",
        )

    logger.info("PATCH entity %r (user_id=%d, type=%r, desc=%r)",
                name, user_id, payload.entity_type, payload.description)
    neo4j = await get_neo4j_client()
    updated = await neo4j.update_entity(
        name=name,
        user_id=user_id,
        entity_type=payload.entity_type,
        description=payload.description,
    )
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"entity not found: {name!r}",
        )
    return updated


@router.delete("/entities/{entity_name:path}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entity(
    entity_name: str = Path(..., description="URL-encoded entity name"),
    current_user: dict = Depends(get_current_user),
):
    """Delete an entity and clean up all references. Idempotent (404 if absent)."""
    user_id = current_user["id"]
    name = unquote(entity_name).strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="entity_name is required",
        )

    logger.info("DELETE entity %r (user_id=%d)", name, user_id)
    neo4j = await get_neo4j_client()
    deleted = await neo4j.delete_entity(name=name, user_id=user_id)
    if deleted == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"entity not found: {name!r}",
        )
    # 204 No Content — body must be empty.
    return None


@router.post("/entities/merge")
async def merge_entities(
    payload: MergeEntityRequest,
    current_user: dict = Depends(get_current_user),
):
    """Merge `source` into `target`. Source is deleted; all MENTIONS and
    RELATES_TO edges are re-pointed to target (with duplicate dedup).

    Returns a count summary so the UI can show "merged N mentions, M
    relations, deleted the source".
    """
    user_id = current_user["id"]
    source = payload.source.strip()
    target = payload.target.strip()
    if source == target:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source and target must be different",
        )

    logger.info("MERGE entity %r → %r (user_id=%d)", source, target, user_id)
    neo4j = await get_neo4j_client()
    try:
        result = await neo4j.merge_entities(
            source_name=source, target_name=target, user_id=user_id,
        )
    except LookupError as e:
        # Source or target doesn't exist for this user.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except ValueError as e:
        # source == target (defensive — should be caught above too).
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )
    return result


# ---------- Entity detail page -----------------------------------------
#
# Single-shot read endpoint backing /entities/:name in the SPA. Composes
# the Neo4j detail envelope with a SQLite hydration pass for the
# `documents.first_seen` field (the documents table is the source of
# truth for created_at; Neo4j doesn't store it).

@router.get("/entities/{entity_name:path}/detail")
async def get_entity_detail(
    entity_name: str = Path(..., description="URL-encoded entity name"),
    current_user: dict = Depends(get_current_user),
):
    """Return the entity detail envelope (entity, stats, documents,
    related_entities, sample_chunks). 404 when the entity doesn't
    exist for this user (or belongs to a different user — same
    response either way, so we never leak existence)."""
    user_id = current_user["id"]
    name = unquote(entity_name).strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="entity_name is required",
        )

    logger.info("GET entity detail: name=%r (user_id=%d)", name, user_id)
    neo4j = await get_neo4j_client()
    envelope = await neo4j.get_entity_detail(name=name, user_id=user_id)
    if envelope is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"entity not found: {name!r}",
        )

    # Hydrate documents[].first_seen from SQLite. The Neo4j query leaves
    # it as None because Document.created_at is owned by the documents
    # table. We do a single batched IN-list lookup — no N+1.
    doc_ids = [d["doc_id"] for d in envelope["documents"]]
    if doc_ids:
        placeholders = ",".join("?" * len(doc_ids))
        async with get_db() as db:
            async with db.execute(
                f"SELECT id, created_at FROM documents "
                f"WHERE id IN ({placeholders}) AND user_id = ?",
                (*doc_ids, user_id),
            ) as cursor:
                rows = await cursor.fetchall()
        created_at_by_id = {row["id"]: row["created_at"] for row in rows}
        for d in envelope["documents"]:
            d["first_seen"] = created_at_by_id.get(d["doc_id"])

    return envelope
