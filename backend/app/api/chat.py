"""Chat API endpoints."""
import json
import logging
import math
import uuid
from typing import List, Dict, Any, AsyncGenerator, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.database import get_db
from app.models.chat import ChatRequest, ChatResponse, Conversation
from app.services.embedding import get_embedding_service
from app.services.chroma_client import get_chroma_client
from app.services.bm25 import get_bm25_service
from app.services.fusion import reciprocal_rank_fusion, deduplicate_results
from app.services.query_processor import get_query_processor
from app.services.neo4j_client import get_neo4j_client
from app.services.llm import get_llm_service
from app.services.reranker import get_rerank_service

router = APIRouter(prefix="/api/chat", tags=["chat"])

logger = logging.getLogger(__name__)


class FeedbackRequest(BaseModel):
    """Submit / replace a 👍/👎 rating on a single assistant message."""
    rating: str = Field(..., pattern="^(up|down)$")
    note: Optional[str] = Field(default=None, max_length=500)


# How many context chunks to expose to the LLM. We cap aggressively so the
# citation markers stay readable — 8 distinct [N] tags in a row is already
# noisy. The reranker has already trimmed to the top_k above this.
_MAX_CITATION_CHUNKS = 8
# How much of the chunk body to ship back to the client so the user can
# actually read the cited passage. The prompt-side cap is separate and
# lives in `per_chunk_chars` below.
_SOURCE_CONTENT_CHARS = 2000
_CITATION_INSTRUCTION = (
    "CITATIONS: After each claim grounded in the provided context, append a "
    "bracket number like [1], [2], [3] that matches the [Context N] tag the "
    "claim came from. You may cite the same source multiple times. If a claim "
    "is not supported by any context, do not cite anything for it. Do not "
    "fabricate numbers that do not appear above."
)


# -------------------------------------------------------------------------
# Citation quality helpers
# -------------------------------------------------------------------------
#
# The reranker returns a 0..1 relevance_score per chunk. We map that to a
# coarse `high / medium / low` band so the front-end can show a coloured
# badge without dealing with raw floats. The thresholds are deliberately
# generous — most siliconflow scores cluster in the 0.3..0.7 range, so
# being too strict would make everything look "low" and useless.
_HIGH_THRESHOLD = 0.70
_MEDIUM_THRESHOLD = 0.40


# Matches "[N]" citation markers in the LLM response body. We count
# unique N values, not raw matches — the LLM might cite [1] three
# times for one claim, but that's still just one source. Compiled once
# at import time so the chat hot path doesn't pay a regex recompile.
import re as _re
_RE_BRACKET_NUM = _re.compile(r"\[(\d+)\]")


def _quality_for_score(score) -> str:
    """Map a 0..1 rerank score to a quality band.

    Anything we can't classify (None, NaN, out of range, wrong type)
    falls through to `medium` so the chat hot path never crashes. The
    point of the badge is to give the user a hint, not to be the
    authoritative judgement of a source's quality.
    """
    if score is None:
        return "medium"
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "medium"
    if math.isnan(s) or math.isinf(s):
        return "medium"
    if s < 0.0 or s > 1.0:
        return "medium"
    if s >= _HIGH_THRESHOLD:
        return "high"
    if s >= _MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def _citation_coverage(num_cited_markers: int, num_sources: int) -> float:
    """Fraction of source-chips that are actually cited in the answer body.

    Used by the front-end to render a "X% of sources are cited" indicator
    so the user can tell at a glance whether the answer is grounded in
    the provided context or just hand-waved. Returns 0.0 when there are
    no sources (nothing to be cited). Clamps the ratio into [0.0, 1.0]
    so a buggy LLM that emits extra `[N]` markers can't break the UI.
    """
    if num_sources <= 0:
        return 0.0
    if num_cited_markers <= 0:
        return 0.0
    ratio = num_cited_markers / num_sources
    if ratio > 1.0:
        return 1.0
    if ratio < 0.0:
        return 0.0
    return ratio


async def _build_citation_context(
    chunks: List[Dict[str, Any]],
    user_id: int,
    max_chunks: int = _MAX_CITATION_CHUNKS,
    per_chunk_chars: int = 600,
    comparison_mode: bool = False,
) -> Dict[str, Any]:
    """Number the top context chunks and produce a prompt-ready string.

    Returns a dict with:
      - context_str: numbered text the LLM sees. When comparison_mode is
        True, each [Context N] block leads with "(from: Doc Title)" so
        the LLM can attribute claims to their source document.
      - sources: list of citation records for the client, with the full
        chunk body (capped at _SOURCE_CONTENT_CHARS) so the user can read
        the cited passage without an extra round-trip.
      - chunk_id_to_index: lookup so callers can resolve any chunk_id back
        to its citation number without re-iterating.
    """
    selected = chunks[:max_chunks]
    parts: List[str] = []
    sources: List[Dict[str, Any]] = []
    chunk_id_to_index: Dict[str, int] = {}

    # Batch-fetch original document titles — the chunks coming back from
    # ChromaDB only carry a document_id, not the title (we didn't store
    # it). One round-trip for all referenced documents beats N lookups.
    doc_ids = list({
        (chunk.get("metadata") or {}).get("document_id")
        or chunk.get("document_id")
        for chunk in selected
    } - {None})
    title_by_doc: Dict[str, str] = {}
    if doc_ids:
        async with get_db() as db:
            placeholders = ",".join("?" * len(doc_ids))
            async with db.execute(
                f"SELECT id, title, original_filename FROM documents "
                f"WHERE id IN ({placeholders}) AND user_id = ?",
                (*doc_ids, user_id),
            ) as cursor:
                rows = await cursor.fetchall()
        # Prefer the user-provided title; fall back to original filename.
        title_by_doc = {
            row["id"]: (row["title"] or row["original_filename"] or "Untitled")
            for row in rows
        }

    for i, chunk in enumerate(selected, start=1):
        chunk_id = chunk.get("chunk_id") or chunk.get("id")
        if chunk_id:
            chunk_id_to_index[chunk_id] = i

        # The chunk body is sent both to the LLM (truncated to keep the
        # prompt readable) and back to the client (truncated to a larger
        # cap so the user can read most of the cited passage). We keep
        # these as separate fields so the prompt can be tightened later
        # without affecting the UI.
        full_content = (chunk.get("content") or "").strip()
        prompt_content = full_content
        if len(prompt_content) > per_chunk_chars:
            prompt_content = prompt_content[:per_chunk_chars].rstrip() + "…"

        meta = chunk.get("metadata") or {}
        document_id = meta.get("document_id") or chunk.get("document_id")
        # Resolve the document title (or "Untitled" if missing) once per
        # chunk so both the prompt context and the sources array can
        # reference it. The lookup may miss if the doc was deleted but
        # the chunk wasn't yet cleaned up; fall back gracefully.
        title = title_by_doc.get(document_id, "Untitled")
        # `meta` stores hierarchy_path as ", "-joined — split it back so
        # the client can render a real breadcrumb.
        raw_path = meta.get("hierarchy_path") or ""
        hierarchy_path = [
            p for p in (s.strip() for s in raw_path.split(",")) if p
        ]

        if comparison_mode:
            # Lead each block with the source document so the LLM can
            # attribute claims to the right doc when answering
            # comparison / contrast questions.
            doc_label = f"(from: {title})" if title else "(from: Untitled)"
            parts.append(f"[Context {i}] {doc_label}\n{prompt_content}")
        else:
            parts.append(f"[Context {i}]\n{prompt_content}")
        # The reranker (run inside build_rag_context) attaches a
        # `relevance_score` (0..1) to each chunk. We forward it to the
        # front-end as a `quality` band so users can see how strong each
        # citation is. If the chunk came from a path that didn't run
        # the reranker (graph-RAG hit, or reranker error fallback),
        # the score is missing and `_quality_for_score` returns
        # "medium" — the safe default.
        raw_score = chunk.get("relevance_score")
        sources.append({
            "index": i,
            "chunk_id": chunk_id,
            "document_id": document_id,
            "title": title_by_doc.get(document_id, "Untitled"),
            "hierarchy_path": hierarchy_path,
            # Full content for the user, truncated only as a payload guard.
            "content": (
                full_content
                if len(full_content) <= _SOURCE_CONTENT_CHARS
                else full_content[:_SOURCE_CONTENT_CHARS].rstrip() + "…"
            ),
            "truncated": len(full_content) > _SOURCE_CONTENT_CHARS,
            # Citation quality — see _quality_for_score for the bands.
            "relevance_score": (
                float(raw_score) if raw_score is not None else None
            ),
            "quality": _quality_for_score(raw_score),
        })

    return {
        "context_str": "\n\n".join(parts),
        "sources": sources,
        "chunk_id_to_index": chunk_id_to_index,
    }


async def build_rag_context(
    query: str,
    user_id: int,
    top_k: int = 5,
    use_hybrid: bool = True,
    use_query_rewrite: bool = True,
    use_graph_rag: bool = False,
    compare_mode: bool = False,
) -> Dict[str, Any]:
    """Build context for RAG from vector and graph retrieval.

    Args:
        query: User query
        user_id: User ID for isolation
        top_k: Number of chunks to retrieve
        use_hybrid: Whether to use hybrid search (BM25 + vector)
        use_query_rewrite: Whether to rewrite query for better retrieval
        use_graph_rag: When True, extract entities from the query and
            build a hard candidate set from chunks that MENTION them
            (Neo4j traversal), then supplement with the hybrid/vector
            results. Falls back silently if no entities are extracted or
            no graph chunks are found.
        compare_mode: When True, the prompt context is grouped by
            document (each [Context N] block leads with the source
            document title) and the system prompt is augmented with
            a COMPARISON instruction asking the LLM to structure the
            answer to highlight cross-document agreements/disagreements.
    """
    # Query preprocessing
    search_query = query
    if use_query_rewrite:
        query_processor = await get_query_processor()
        rewritten = await query_processor.rewrite_query(query)
        if rewritten and len(rewritten) > 0:
            search_query = rewritten

    # Get query embedding
    embedding_service = await get_embedding_service()
    query_embedding = await embedding_service.embed_single(search_query)

    # Hybrid search
    chroma = get_chroma_client()
    bm25 = get_bm25_service()
    neo4j = await get_neo4j_client()

    # ---------- Optional graph-RAG candidate set ----------
    # When enabled, ask the graph: "which chunks MENTION any of the
    # entities the user is asking about?" The result is a hard-filtered
    # candidate list; if it has fewer than top_k hits we fill the rest
    # from the usual vector+BM25 path (deduped) so we never deliver
    # fewer candidates than the reranker needs.
    graph_chunks: List[Dict[str, Any]] = []
    if use_graph_rag:
        try:
            query_processor = await get_query_processor()
            extracted = await query_processor.extract_entities(search_query)
            entity_names = [e["name"] for e in (extracted or []) if e.get("name")]
            if entity_names:
                graph_chunk_ids = await neo4j.get_chunks_for_entities(
                    entity_names=entity_names,
                    user_id=user_id,
                    limit=max(top_k * 4, 20),
                )
                if graph_chunk_ids:
                    graph_chunks = chroma.get_chunks_by_ids(graph_chunk_ids, user_id)
                    logger.info(
                        "graph_rag: extracted %d entities (%s) → %d graph chunks",
                        len(entity_names), entity_names[:5], len(graph_chunks),
                    )
        except Exception as e:
            # Graph-RAG is an optimization; never let it break the
            # primary retrieval path.
            logger.warning("graph_rag: extraction failed, falling back: %s", e)

    # ---------- Vector + BM25 path (always run; will be deduped if graph_rag adds candidates) ----------
    if use_hybrid:
        # Check if user has BM25 index, if not, build it
        if not bm25.has_index(user_id):
            # Build BM25 index from SQLite for this user
            async with get_db() as db:
                async with db.execute(
                    "SELECT chunk_id, content FROM chunks WHERE user_id = ?",
                    (user_id,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    if rows:
                        chunk_contents = [r["content"] for r in rows]
                        chunk_ids = [r["chunk_id"] for r in rows]
                        bm25.build_user_index(user_id, chunk_contents, chunk_ids)

        # Vector search (larger recall for fusion)
        vector_results = chroma.search(query_embedding, user_id, top_k=50)

        # BM25 search
        bm25_results = bm25.search(search_query, user_id, top_k=50)

        # RRF fusion
        fused_results = reciprocal_rank_fusion(
            vector_results,
            bm25_results,
            k=60,
            top_k=50
        )
        hybrid_chunks = fused_results
    else:
        # Original vector-only search
        hybrid_chunks = chroma.search(query_embedding, user_id, top_k=20)

    # ---------- Merge: graph hits first (boost), then hybrid dedup ----------
    if graph_chunks:
        seen = {c.get("chunk_id") for c in graph_chunks}
        # Give graph hits a small synthetic rank boost by prepending them
        # before the hybrid set. The reranker will re-score from scratch.
        merged = list(graph_chunks) + [
            c for c in hybrid_chunks if c.get("chunk_id") not in seen
        ]
        chunks = merged
    else:
        chunks = hybrid_chunks

    # Rerank to get top_k most relevant
    rerank_service = await get_rerank_service()
    chunks = await rerank_service.rerank(search_query, chunks, top_k=top_k)

    # Get context chunks
    all_chunks = []
    for chunk in chunks:
        all_chunks.append(chunk)
        context = chroma.get_chunk_context(chunk["chunk_id"], user_id, window_size=1)
        all_chunks.extend(context)

    # Get entities from chunks
    chunk_ids = [c["chunk_id"] for c in all_chunks]
    entities = await neo4j.get_entities_from_chunks(chunk_ids, user_id)

    # Get related entities from graph
    entity_names = [e["name"] for e in entities]
    relations = []
    if entity_names:
        graph_data = await neo4j.get_related_entities(entity_names[:3], user_id, depth=2)
        relations = graph_data.get("relations", [])
        # Add related entities to list
        for rel in relations:
            if not any(e["name"] == rel["source"] for e in entities):
                entities.append({"name": rel["source"], "type": "Related"})
            if not any(e["name"] == rel["target"] for e in entities):
                entities.append({"name": rel["target"], "type": "Related"})

    return {
        "chunks": all_chunks,
        "entities": entities,
        "relations": relations
    }


@router.post("")
async def chat(
    request: ChatRequest,
    current_user: dict = Depends(get_current_user)
):
    """Non-streaming chat with RAG."""
    user_id = current_user["id"]

    # Get or create conversation
    if request.conversation_id:
        conversation_id = request.conversation_id
    else:
        conversation_id = str(uuid.uuid4())
        async with get_db() as db:
            await db.execute(
                "INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
                (conversation_id, user_id, request.message[:50])
            )
            await db.commit()

    # Save user message
    async with get_db() as db:
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, "user", request.message)
        )
        await db.commit()

    # Build context
    if request.include_context:
        context = await build_rag_context(
            request.message,
            user_id,
            use_graph_rag=request.use_graph_rag,
            compare_mode=request.compare_mode,
        )
    else:
        context = {"chunks": [], "entities": [], "relations": []}

    # Get conversation history
    conversation_history = []
    async with get_db() as db:
        async with db.execute(
            "SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY created_at LIMIT 10",
            (conversation_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            conversation_history = [{"role": r["role"], "content": r["content"]} for r in rows]

    # Build a numbered citation context for the prompt
    if context["chunks"]:
        citation = await _build_citation_context(
            context["chunks"], user_id, comparison_mode=request.compare_mode,
        )
    else:
        citation = {
            "context_str": "",
            "sources": [],
            "chunk_id_to_index": {},
        }

    # Generate response — feed in the pre-built numbered context rather than
    # letting the LLM service re-format the chunks, so the prompt and the
    # citation sources are guaranteed to use the same numbering.
    llm_service = await get_llm_service()
    response = await llm_service.generate_rag_response(
        query=request.message,
        context_chunks=context["chunks"],
        related_entities=context["entities"],
        related_relations=context["relations"],
        conversation_history=conversation_history[:-1],  # Exclude current message
        custom_context_str=citation["context_str"],
        citation_instruction=_CITATION_INSTRUCTION if citation["sources"] else None,
        comparison_mode=request.compare_mode,
    )

    # Save assistant message
    async with get_db() as db:
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, "assistant", response)
        )
        await db.commit()

    # Compute how many of the available source chips the LLM actually
    # cited. Distinct [N] markers in the body → unique source indices
    # actually referenced. Coverage is the ratio of cited to available.
    cited_markers = set()
    if citation["sources"]:
        for m in _RE_BRACKET_NUM.finditer(response or ""):
            try:
                idx = int(m.group(1))
            except ValueError:
                continue
            if 1 <= idx <= len(citation["sources"]):
                cited_markers.add(idx)
    coverage = _citation_coverage(
        num_cited_markers=len(cited_markers),
        num_sources=len(citation["sources"]),
    )

    # Optional follow-up chips — same semantics as the streaming path.
    # Failure is non-fatal; the client just renders zero chips.
    followups: List[str] = []
    if request.with_followups:
        try:
            followups = await llm_service.generate_followups(
                request.message, response, n=3,
            )
        except Exception as e:
            logger.warning("generate_followups failed: %s", e)
            followups = []

    return {
        "message": response,
        "conversation_id": conversation_id,
        "related_chunks": context["chunks"][:3],
        "related_entities": context["entities"][:5],
        "sources": citation["sources"],
        "followups": followups,
        "citation_coverage": coverage,
    }


async def chat_stream_generator(
    request: ChatRequest,
    user_id: int
) -> AsyncGenerator[str, None]:
    """Generator for streaming chat responses."""
    # Get or create conversation
    if request.conversation_id:
        conversation_id = request.conversation_id
    else:
        conversation_id = str(uuid.uuid4())
        async with get_db() as db:
            await db.execute(
                "INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
                (conversation_id, user_id, request.message[:50])
            )
            await db.commit()

    # Save user message
    async with get_db() as db:
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, "user", request.message)
        )
        await db.commit()

    # Build context
    if request.include_context:
        context = await build_rag_context(
            request.message,
            user_id,
            use_graph_rag=request.use_graph_rag,
            compare_mode=request.compare_mode,
        )
    else:
        context = {"chunks": [], "entities": [], "relations": []}

    # Build a numbered citation context for the prompt
    if context["chunks"]:
        citation = await _build_citation_context(
            context["chunks"], user_id, comparison_mode=request.compare_mode,
        )
    else:
        citation = {
            "context_str": "",
            "sources": [],
            "chunk_id_to_index": {},
        }

    # Push the sources FIRST so the client can render citation chips while
    # the text is still streaming. Sources are tied to a query, not a
    # response, so sending them before the body is safe — if the stream is
    # cancelled, the sources are still meaningful for the next attempt.
    if citation["sources"]:
        yield f"event: sources\ndata: {json.dumps({'sources': citation['sources']})}\n\n"

    # Build messages for LLM
    system_prompt = f"""You are a helpful assistant. Answer based on the following context. If the answer is not in the context, say so clearly.

Context:
{citation['context_str']}

{_CITATION_INSTRUCTION if citation['sources'] else ''}"""

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": request.message}
    ]

    # Stream response
    llm_service = await get_llm_service()
    full_response = []

    async for chunk in llm_service.chat_complete_stream(messages):
        full_response.append(chunk)
        yield f"data: {chunk}\n\n"

    # Save complete response
    complete_response = "".join(full_response)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, "assistant", complete_response)
        )
        await db.commit()

    # Compute citation coverage (same logic as the non-streaming path).
    cited_markers: set = set()
    if citation["sources"]:
        for m in _RE_BRACKET_NUM.finditer(complete_response or ""):
            try:
                idx = int(m.group(1))
            except ValueError:
                continue
            if 1 <= idx <= len(citation["sources"]):
                cited_markers.add(idx)
    coverage = _citation_coverage(
        num_cited_markers=len(cited_markers),
        num_sources=len(citation["sources"]),
    )

    # Optional follow-up chips — emit as a separate SSE event AFTER the
    # body so the client can render the answer first, then surface the
    # chips below it. Failure is non-fatal (generate_followups swallows
    # its own errors); we still emit the 'done' event either way.
    if request.with_followups:
        followups: List[str] = []
        try:
            followups = await llm_service.generate_followups(
                request.message, complete_response, n=3,
            )
        except Exception as e:
            logger.warning("generate_followups failed: %s", e)
            followups = []
        yield f"event: followups\ndata: {json.dumps({'followups': followups})}\n\n"

    yield f"event: done\ndata: {json.dumps({'conversation_id': conversation_id, 'sources': citation['sources'], 'citation_coverage': coverage})}\n\n"


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    current_user: dict = Depends(get_current_user)
):
    """Streaming chat with RAG."""
    user_id = current_user["id"]

    return StreamingResponse(
        chat_stream_generator(request, user_id),
        media_type="text/event-stream"
    )


@router.get("/conversations", response_model=List[Conversation])
async def list_conversations(
    current_user: dict = Depends(get_current_user)
):
    """List user's conversations."""
    user_id = current_user["id"]

    async with get_db() as db:
        async with db.execute(
            "SELECT id, user_id, title, created_at FROM conversations WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()

    return [dict(row) for row in rows]


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get messages for a conversation."""
    user_id = current_user["id"]

    async with get_db() as db:
        # Verify ownership
        async with db.execute(
            "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id)
        ) as cursor:
            if not await cursor.fetchone():
                raise HTTPException(status_code=404, detail="Conversation not found")

        # Return id so the client can correlate feedback to a specific message
        async with db.execute(
            "SELECT id, role, content, created_at FROM messages WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,)
        ) as cursor:
            rows = await cursor.fetchall()

    return [dict(row) for row in rows]


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete a conversation."""
    user_id = current_user["id"]

    async with get_db() as db:
        # Verify ownership and delete
        async with db.execute(
            "DELETE FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id)
        ) as cursor:
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Conversation not found")
        await db.commit()

    return {"message": "Conversation deleted"}


# ---------- Message feedback (👍 / 👎) ---------------------------------------
#
# We deliberately do NOT require the message itself to exist in any user-
# facing route — the FK is enforced at write time, but a stale or deleted
# message id (e.g. after a hard delete outside the API) will 404 cleanly
# rather than 500. The CASCADE on the FK still cleans up feedback rows
# when a message is deleted via ON DELETE CASCADE.
async def _verify_message_owner(db, message_id: int, user_id: int) -> Optional[str]:
    """Return the message's conversation_id if it belongs to the user, else None."""
    async with db.execute(
        """
        SELECT m.conversation_id
        FROM messages m
        JOIN conversations c ON c.id = m.conversation_id
        WHERE m.id = ? AND c.user_id = ?
        """,
        (message_id, user_id),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    return row["conversation_id"]


@router.post("/messages/{message_id}/feedback")
async def submit_feedback(
    message_id: int,
    payload: FeedbackRequest,
    current_user: dict = Depends(get_current_user),
):
    """Insert or replace the current user's rating for an assistant message."""
    user_id = current_user["id"]

    async with get_db() as db:
        conversation_id = await _verify_message_owner(db, message_id, user_id)
        if conversation_id is None:
            raise HTTPException(status_code=404, detail="Message not found")

        # UPSERT: re-rating replaces the previous one. Returning the row id
        # lets the client confirm the write without an extra GET.
        await db.execute(
            """
            INSERT INTO message_feedback
                (message_id, user_id, conversation_id, rating, note)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(message_id, user_id) DO UPDATE SET
                rating = excluded.rating,
                note = excluded.note,
                created_at = CURRENT_TIMESTAMP
            """,
            (message_id, user_id, conversation_id, payload.rating, payload.note),
        )
        await db.commit()

    return {
        "message_id": message_id,
        "rating": payload.rating,
        "note": payload.note,
    }


@router.get("/messages/{message_id}/feedback")
async def get_feedback(
    message_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Return the current user's rating for the message, or {rating: null}."""
    user_id = current_user["id"]

    async with get_db() as db:
        conversation_id = await _verify_message_owner(db, message_id, user_id)
        if conversation_id is None:
            raise HTTPException(status_code=404, detail="Message not found")

        async with db.execute(
            """
            SELECT rating, note, created_at FROM message_feedback
            WHERE message_id = ? AND user_id = ?
            """,
            (message_id, user_id),
        ) as cursor:
            row = await cursor.fetchone()

    if not row:
        return {"message_id": message_id, "rating": None, "note": None}
    return {
        "message_id": message_id,
        "rating": row["rating"],
        "note": row["note"],
        "created_at": row["created_at"],
    }


@router.delete("/messages/{message_id}/feedback", status_code=204)
async def delete_feedback(
    message_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Clear the current user's rating for the message."""
    user_id = current_user["id"]

    async with get_db() as db:
        # Don't 404 on the ownership check here — clearing a non-existent
        # rating is idempotent. Only the SQL rowcount matters.
        await db.execute(
            "DELETE FROM message_feedback WHERE message_id = ? AND user_id = ?",
            (message_id, user_id),
        )
        await db.commit()
    return None
