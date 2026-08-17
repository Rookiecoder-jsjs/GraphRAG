"""Chat API endpoints."""
import json
import logging
import math
import time
import uuid
from typing import List, Dict, Any, AsyncGenerator, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.auth.rate_limit import chat_limiter, enforce_rate_limit
from app.services.intent import classify_intent
from app.config import get_settings
from app.database import get_db
from app.models.chat import ChatRequest, ChatResponse, Conversation
from app.services.embedding import get_embedding_service
from app.services.chroma_client import get_chroma_client
from app.services.bm25 import get_bm25_service
from app.services.fusion import reciprocal_rank_fusion, deduplicate_results
from app.services.query_processor import get_query_processor
from app.services.neo4j_client import get_neo4j_client
from app.services.llm import (
    _CITATION_INSTRUCTION,
    build_rag_system_prompt,
    get_llm_service,
)
from app.services.reranker import get_rerank_service

router = APIRouter(prefix="/api/chat", tags=["chat"])

logger = logging.getLogger(__name__)


class FeedbackRequest(BaseModel):
    """Submit / replace a 👍/👎 rating on a single assistant message."""
    rating: str = Field(..., pattern="^(up|down)$")
    note: Optional[str] = Field(default=None, max_length=500)


async def _resolve_conversation_id(
    conversation_id: Optional[str], user_id: int, title: str
) -> str:
    """Return a conversation_id verified to belong to ``user_id``, creating one if absent.

    SECURITY: when a ``conversation_id`` is supplied we must confirm ownership
    before writing. Without this check any authenticated user could inject
    messages into another user's conversation and — because the chat path loads
    conversation history into the LLM prompt — read that user's content back
    through the model's answer. A missing/foreign conversation 404s.
    """
    if conversation_id:
        async with get_db() as db:
            async with db.execute(
                "SELECT id FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            ) as cursor:
                if not await cursor.fetchone():
                    raise HTTPException(status_code=404, detail="Conversation not found")
        return conversation_id

    new_id = str(uuid.uuid4())
    async with get_db() as db:
        await db.execute(
            "INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
            (new_id, user_id, title),
        )
        await db.commit()
    return new_id


# How many context chunks to expose to the LLM. We cap aggressively so the
# citation markers stay readable — 8 distinct [N] tags in a row is already
# noisy. The reranker has already trimmed to the top_k above this.
_MAX_CITATION_CHUNKS = 8
# How much of the chunk body to ship back to the client so the user can
# actually read the cited passage. The prompt-side cap is separate and
# lives in `per_chunk_chars` below.
_SOURCE_CONTENT_CHARS = 2000
# Typed refusal for queries the intent router classifies as should_reject
# (opinions / advice / unsafe / out-of-scope). Without this the classified
# intent produced no behavioural difference - the model free-generated an
# answer from an empty context, defeating the point of the classification.
_REJECTION_TEMPLATE = (
    "抱歉，这个问题超出了我能回答的范围——我专注于基于你知识库文档的"
    "事实性问答。请尝试把它改写成与文档内容相关的事实性问题。"
)
# Lightweight system prompt for queries the intent router classified as
# chitchat (greetings / small talk / thanks). Deliberately NO <context> block
# and NO citation instruction — the model should give a friendly, brief reply
# rather than pretending it searched the knowledge base. (The RAG prompt with
# an empty context would instead produce a "context is empty, so I can't
# answer" refusal-style reply.)
_CHITCHAT_SYSTEM_PROMPT = (
    "你是知识库助手的闲聊模式。用户正在问候、寒暄或闲聊，而不是提问知识库内容。"
    "请用友好、简短、自然的中文回应（一两句话即可），可以顺势邀请用户提问文档相关的问题；"
    "不要假装检索过任何文档，不要编造知识库内容，不要长篇大论。"
)


async def _save_assistant_message(
    conversation_id: str,
    content: str,
    sources: List[Dict[str, Any]],
) -> None:
    """Persist one assistant turn plus its citation-source rows."""
    async with get_db() as db:
        cur = await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, "assistant", content)
        )
        msg_id = cur.lastrowid
        if msg_id and sources:
            await db.executemany(
                "INSERT OR IGNORE INTO message_sources (message_id, chunk_id, rank) VALUES (?, ?, ?)",
                [
                    (msg_id, s.get("chunk_id"), s.get("index"))
                    for s in sources if s.get("chunk_id")
                ],
            )
        await db.commit()


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
) -> Dict[str, Any]:
    """Number the top context chunks and produce a prompt-ready string.

    Returns a dict with:
      - context_str: numbered text the LLM sees. Every [Context N] block
        leads with "(from: Doc Title)" — even outside COMPARISON mode — so
        the LLM can attribute claims to a source document and answer
        cross-document questions coherently. When the reranker scored a
        chunk, the block also carries a coarse relevance band so the model
        can weight stronger sources.
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

        # Every context block leads with the source document title (even
        # outside COMPARISON mode) so the model can attribute claims to a
        # specific doc and answer cross-document questions coherently.
        doc_label = f"(from: {title})" if title else "(from: Untitled)"
        # The reranker (run inside build_rag_context) attaches a
        # `relevance_score` (0..1) to each chunk. Surface a coarse band in
        # the prompt so the model can weight stronger sources, and forward
        # the same score to the front-end as a `quality` band. If the
        # chunk came from a path that didn't run the reranker (graph-RAG
        # hit, or reranker error fallback), the score is missing and
        # `_quality_for_score` returns "medium" — the safe default.
        raw_score = chunk.get("relevance_score")
        score_label = (
            f" (relevance: {_quality_for_score(raw_score)})"
            if raw_score is not None else ""
        )
        parts.append(f"[Context {i}] {doc_label}{score_label}\n{prompt_content}")
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
    from app.services.retriever import retrieve
    return await retrieve(
        query, user_id,
        top_k=top_k,
        use_graph_rag=use_graph_rag,
        conversation_history=None,
        enable_rewrite=use_query_rewrite,
    )


@router.post("")
async def chat(
    request: ChatRequest,
    current_user: dict = Depends(get_current_user)
):
    """Non-streaming chat with RAG."""
    user_id = current_user["id"]
    # Throttle billable LLM work per user (no-op under test).
    enforce_rate_limit(chat_limiter, f"chat:{user_id}")

    # Verify ownership (or create) before writing into the conversation.
    conversation_id = await _resolve_conversation_id(
        request.conversation_id, user_id, request.message[:50]
    )

    # Save user message
    async with get_db() as db:
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, "user", request.message)
        )
        await db.commit()

    # Get conversation history (10 most recent, chronological) FIRST so it
    # can feed both retrieval (conversational rewrite) and generation.
    conversation_history = []
    async with get_db() as db:
        async with db.execute(
            "SELECT role, content FROM messages "
            "WHERE conversation_id = ? ORDER BY created_at DESC, id DESC LIMIT 10",
            (conversation_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        conversation_history = [
            {"role": r["role"], "content": r["content"]} for r in reversed(rows)
        ]

    # Intent routing: chitchat / should_reject skip retrieval (the LLM
    # answers directly or refuses); only fact_retrieval pays for RAG.
    # Classification failure falls back to fact_retrieval.
    intent = await classify_intent(request.message)
    if intent["intent"] == "should_reject":
        # Typed refusal - see _REJECTION_TEMPLATE. Saved as a real assistant
        # turn so the conversation history stays coherent.
        await _save_assistant_message(conversation_id, _REJECTION_TEMPLATE, [])
        return {
            "message": _REJECTION_TEMPLATE,
            "conversation_id": conversation_id,
            "related_chunks": [],
            "related_entities": [],
            "sources": [],
            "citation_coverage": 0.0,
        }
    if intent["intent"] == "chitchat":
        # Chitchat: skip retrieval and answer with a dedicated light prompt —
        # no <context> block, no citation instruction — so a casual greeting
        # gets a friendly short reply instead of the "context is empty, so I
        # can't answer" refusal the RAG prompt produces with zero chunks.
        llm_service = await get_llm_service()
        chitchat_response = await llm_service.chat_complete(
            [
                {"role": "system", "content": _CHITCHAT_SYSTEM_PROMPT},
                {"role": "user", "content": request.message},
            ],
            temperature=0.7,
            max_tokens=500,
        )
        # content=null (e.g. safety-blocked responses) already normalizes to
        # "" inside chat_complete; never persist/return None.
        chitchat_response = chitchat_response or ""
        await _save_assistant_message(conversation_id, chitchat_response, [])
        return {
            "message": chitchat_response,
            "conversation_id": conversation_id,
            "related_chunks": [],
            "related_entities": [],
            "sources": [],
            "citation_coverage": 0.0,
        }
    if intent["intent"] == "fact_retrieval" and request.include_context:
        from app.services.retriever import retrieve
        context = await retrieve(
            request.message,
            user_id,
            use_graph_rag=request.use_graph_rag,
            conversation_history=conversation_history[:-1],
        )
    else:
        context = {"chunks": [], "entities": [], "relations": []}

    # Build a numbered citation context for the prompt
    if context["chunks"]:
        citation = await _build_citation_context(
            context["chunks"], user_id,
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

    # content=null (e.g. safety-blocked responses) already normalizes to ""
    # inside chat_complete; belt-and-suspenders so we never persist/return
    # None (the messages.content column is NOT NULL).
    response = response or ""

    # Save assistant message + the chunk_ids it cited (for feedback attribution)
    await _save_assistant_message(conversation_id, response, citation["sources"])

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

    return {
        "message": response,
        "conversation_id": conversation_id,
        "related_chunks": context["chunks"][:3],
        "related_entities": context["entities"][:5],
        "sources": citation["sources"],
        "citation_coverage": coverage,
    }


async def _chat_stream_body(
    request: ChatRequest,
    user_id: int,
    conversation_id: str,
) -> AsyncGenerator[str, None]:
    """Body of the streaming chat response (wrapped by chat_stream_generator).

    ``conversation_id`` is resolved and ownership-verified by the caller
    (``chat_stream``) BEFORE this generator starts, so a foreign conversation
    yields a clean 404 instead of a half-opened SSE stream.
    """
    # Save user message
    async with get_db() as db:
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (conversation_id, "user", request.message)
        )
        await db.commit()

    # Fetch prior turns for the conversational query rewrite.
    conversation_history: list = []
    async with get_db() as db:
        async with db.execute(
            "SELECT role, content FROM messages "
            "WHERE conversation_id = ? ORDER BY created_at DESC, id DESC LIMIT 10",
            (conversation_id,)
        ) as cursor:
            rows = await cursor.fetchall()
        conversation_history = [
            {"role": r["role"], "content": r["content"]} for r in reversed(rows)
        ]

    # Intent routing: chitchat / should_reject skip retrieval; only
    # fact_retrieval pays for RAG. Failure falls back to fact_retrieval.
    intent = await classify_intent(request.message)
    if intent["intent"] == "should_reject":
        # Typed refusal (see _REJECTION_TEMPLATE): stream it as a normal
        # answer, persist it, and finish. Classified refusals used to
        # free-generate from an empty context instead.
        yield f"data: {json.dumps({'chunk': _REJECTION_TEMPLATE})}\n\n"
        await _save_assistant_message(conversation_id, _REJECTION_TEMPLATE, [])
        yield f"event: done\ndata: {json.dumps({'conversation_id': conversation_id, 'sources': [], 'citation_coverage': 0.0})}\n\n"
        return
    if intent["intent"] == "fact_retrieval" and request.include_context:
        from app.services.retriever import retrieve
        context = await retrieve(
            request.message,
            user_id,
            use_graph_rag=request.use_graph_rag,
            conversation_history=conversation_history[:-1],
        )
    else:
        context = {"chunks": [], "entities": [], "relations": []}

    # Build a numbered citation context for the prompt
    t_cite_start = time.perf_counter()
    if context["chunks"]:
        citation = await _build_citation_context(
            context["chunks"], user_id,
        )
    else:
        citation = {
            "context_str": "",
            "sources": [],
            "chunk_id_to_index": {},
        }
    t_cite_end = time.perf_counter()

    # Push the sources FIRST so the client can render citation chips while
    # the text is still streaming. Sources are tied to a query, not a
    # response, so sending them before the body is safe — if the stream is
    # cancelled, the sources are still meaningful for the next attempt.
    if citation["sources"]:
        yield f"event: sources\ndata: {json.dumps({'sources': citation['sources']})}\n\n"

    # Build messages for LLM. Chitchat gets a dedicated light prompt — no
    # <context> block, no citation instruction — so casual greetings get a
    # friendly short reply instead of the "context is empty, so I can't
    # answer" refusal the RAG prompt produces with zero chunks.
    if intent["intent"] == "chitchat":
        system_prompt = _CHITCHAT_SYSTEM_PROMPT
    else:
        # Same single RAG prompt source the non-streaming path uses
        # (build_rag_system_prompt in services/llm.py) — so the two can
        # never drift apart again. The retrieved document text is UNTRUSTED
        # user-uploaded content: the template delimits it in <context> and
        # instructs the model to treat it as data, never as instructions
        # (indirect prompt injection). Graph facts ride along too.
        system_prompt = build_rag_system_prompt(
            context_str=citation["context_str"],
            related_entities=context["entities"],
            related_relations=context["relations"],
            citation_instruction=(
                _CITATION_INSTRUCTION if citation["sources"] else None
            ),
            comparison_mode=request.compare_mode,
        )

    # Prior turns (excluding the just-saved current user message), same
    # 5-turn window the non-streaming path uses. The streaming path used to
    # drop history entirely, so follow-up questions lost their referent.
    generation_history = [
        {"role": m["role"], "content": m["content"]}
        for m in conversation_history[:-1][-5:]
    ]
    messages = [
        {"role": "system", "content": system_prompt},
        *generation_history,
        {"role": "user", "content": request.message}
    ]

    # Stream response
    llm_service = await get_llm_service()
    full_response = []
    thinking_parts = []
    t_stream_start = time.perf_counter()
    t_first_byte: Optional[float] = None
    stream_error: Optional[str] = None

    # chat_complete_stream yields (kind, text) tuples: "thinking" frames
    # carry the model's reasoning (hybrid-thinking mode only) and get their
    # own SSE event so the client renders them in a separate collapsible
    # block; the first-byte metric below stays anchored to the first
    # "content" chunk so the two modes are directly comparable.
    async for kind, text in llm_service.chat_complete_stream(
        messages, enable_thinking=request.enable_thinking
    ):
        if not text:
            continue  # defensive: never forward None/empty deltas downstream
        if kind == "error":
            # Provider failure: surface a structured terminal error frame.
            # The error text is never appended to the answer (it used to be
            # streamed as content, saved to history, and fed back into the
            # next turn's prompt).
            stream_error = text
            yield f"event: error\ndata: {json.dumps({'error': text})}\n\n"
            break
        if kind == "thinking":
            thinking_parts.append(text)
            yield f"event: thinking\ndata: {json.dumps({'text': text})}\n\n"
            continue
        if t_first_byte is None:
            t_first_byte = time.perf_counter()
        full_response.append(text)
        # JSON-encode the chunk: a raw newline in the model output would
        # otherwise terminate the SSE `data:` field mid-token and corrupt
        # the frame (dropping/garbling text on the client).
        yield f"data: {json.dumps({'chunk': text})}\n\n"

    t_stream_end = time.perf_counter()
    # Full server-side attribution for one turn, pairing the in-context
    # build_rag_context line: first_byte here minus that total there is the
    # LLM provider's own prefill/queue latency (the part we don't control).
    logger.info(
        "chat_stream timing: citation=%.3fs first_byte=%.3fs stream=%.3fs "
        "turn=%.3fs (context_chunks=%d, answer_chars=%d, thinking_chars=%d)",
        t_cite_end - t_cite_start,
        (t_first_byte or t_stream_end) - t_stream_start,
        t_stream_end - t_stream_start,
        t_stream_end - t_cite_start,
        len(context["chunks"]),
        sum(len(c) for c in full_response),
        sum(len(t) for t in thinking_parts),
    )

    # Persist the turn. Whatever streamed before a provider failure is real
    # content and is kept; the error TEXT itself never enters history.
    # Nothing streamed + error -> save nothing (an empty placeholder
    # assistant message would only pollute the next turn's prompt).
    complete_response = "".join(full_response)
    if complete_response or stream_error is None:
        await _save_assistant_message(
            conversation_id, complete_response, citation.get("sources") or []
        )

    if stream_error is not None:
        # Terminal error frame already emitted above; `done` means success.
        return

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

    # The answer is fully streamed, saved, and coverage is computed — the
    # turn is logically complete.
    yield f"event: done\ndata: {json.dumps({'conversation_id': conversation_id, 'sources': citation['sources'], 'citation_coverage': coverage})}\n\n"


async def chat_stream_generator(
    request: ChatRequest,
    user_id: int,
    conversation_id: str,
) -> AsyncGenerator[str, None]:
    """Streaming chat responses, with a guaranteed terminal frame.

    Wraps :func:`_chat_stream_body` so any unexpected mid-stream failure
    (retrieval crash, DB write error) surfaces as a terminal ``event: error``
    SSE frame instead of silently dropping the connection. GeneratorExit
    (client disconnect) is a BaseException and is deliberately not caught.
    """
    try:
        async for frame in _chat_stream_body(request, user_id, conversation_id):
            yield frame
    except Exception as e:
        logger.error("chat stream aborted: %s", e, exc_info=True)
        yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    current_user: dict = Depends(get_current_user)
):
    """Streaming chat with RAG."""
    user_id = current_user["id"]
    # Throttle billable LLM work per user (no-op under test).
    enforce_rate_limit(chat_limiter, f"chat:{user_id}")

    # Verify ownership (or create) BEFORE opening the stream, so a foreign
    # conversation_id returns a clean 404 instead of a half-opened response.
    conversation_id = await _resolve_conversation_id(
        request.conversation_id, user_id, request.message[:50]
    )

    return StreamingResponse(
        chat_stream_generator(request, user_id, conversation_id),
        media_type="text/event-stream"
    )


@router.get("/conversations", response_model=List[Conversation])
async def list_conversations(
    current_user: dict = Depends(get_current_user)
):
    """List user's conversations.

    Enriched with message count, last-message preview and last-activity time
    (one query, no N+1) and ordered by last activity so the most recently
    touched conversation surfaces first in the history-management page.
    """
    user_id = current_user["id"]

    async with get_db() as db:
        async with db.execute(
            """
            SELECT c.id, c.user_id, c.title, c.created_at,
                   (SELECT COUNT(*) FROM messages m
                     WHERE m.conversation_id = c.id) AS message_count,
                   (SELECT m.content FROM messages m
                     WHERE m.conversation_id = c.id
                     ORDER BY m.created_at DESC, m.id DESC LIMIT 1) AS last_message,
                   (SELECT MAX(m.created_at) FROM messages m
                     WHERE m.conversation_id = c.id) AS last_activity
            FROM conversations c
            WHERE c.user_id = ?
            ORDER BY COALESCE(last_activity, c.created_at) DESC
            """,
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
