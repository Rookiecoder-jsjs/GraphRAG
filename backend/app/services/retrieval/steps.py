"""Retrieval pipeline steps — one class per retrievable operation.

Each step is stateless and reads/writes a :class:`RetrievalContext`.
Steps self-skip on preconditions (``use_hybrid`` / ``use_graph_rag`` /
min-length gates) so the pipeline config stays declarative: the same
step list runs whether or not a flag is set.

The bodies are verbatim moves of the inline logic that used to live in
``app/api/chat.py:build_rag_context`` and ``app/api/search.py:search`` —
behavior is unchanged, only the home moved.
"""
import logging
from typing import Any, Dict, List, Type

from app.database import get_db
from app.services.bm25 import get_bm25_service
from app.services.chroma_client import get_chroma_client
from app.services.embedding import get_embedding_service
from app.services.fusion import reciprocal_rank_fusion
from app.services.neo4j_client import get_neo4j_client
from app.services.query_processor import get_query_processor
from app.services.reranker import get_rerank_service

from app.services.retrieval.context import RetrievalContext

logger = logging.getLogger(__name__)


class RetrievalStep:
    """Base class: every step is ``await step(ctx) -> ctx``."""

    name: str = ""

    async def __call__(self, ctx: RetrievalContext) -> RetrievalContext:
        raise NotImplementedError


STEP_REGISTRY: Dict[str, Type[RetrievalStep]] = {}


def register_step(name: str):
    """Class decorator: register a step class under ``name``."""

    def deco(cls):
        cls.name = name
        STEP_REGISTRY[name] = cls
        return cls

    return deco


@register_step("query_rewrite")
class QueryRewriteStep(RetrievalStep):
    """Rewrite the query for better retrieval (chat only).

    When the graph lane is active and CHAT_COMBINED_REWRITE_EXTRACT is
    on, ONE LLM call produces both the rewritten query and the entities
    to look up (halving the blocking LLM latency of the old two-call
    flow); GraphRetrieveStep reuses ``ctx.query_entities``. Otherwise
    the standalone rewrite runs as before.
    """

    async def __call__(self, ctx: RetrievalContext) -> RetrievalContext:
        # Rewriting costs a full LLM round-trip (~1s) that BLOCKS every
        # retrieval step after it, so only pay it for queries long enough
        # to plausibly benefit — short keyword-style questions (the demo
        # common case) go straight to retrieval. Tunable via
        # QUERY_REWRITE_MIN_LEN (0 = always rewrite).
        search_query = ctx.query
        if ctx.use_query_rewrite and len(ctx.query.strip()) >= ctx.settings.QUERY_REWRITE_MIN_LEN:
            query_processor = await get_query_processor()
            if (
                ctx.use_graph_rag
                and getattr(ctx.settings, "CHAT_COMBINED_REWRITE_EXTRACT", True)
                and hasattr(query_processor, "rewrite_and_extract")
            ):
                combined = await query_processor.rewrite_and_extract(ctx.query)
                rewritten = (combined.get("rewritten") or "").strip()
                if rewritten:
                    search_query = rewritten
                ctx.query_entities = combined.get("entities") or []
            else:
                rewritten = await query_processor.rewrite_query(ctx.query)
                if rewritten and len(rewritten) > 0:
                    search_query = rewritten
        ctx.search_query = search_query
        return ctx


@register_step("query_embed")
class QueryEmbedStep(RetrievalStep):
    """Embed the (possibly rewritten) query."""

    async def __call__(self, ctx: RetrievalContext) -> RetrievalContext:
        embedding_service = await get_embedding_service()
        ctx.query_embedding = await embedding_service.embed_single(
            ctx.search_query or ctx.query
        )
        return ctx


@register_step("vector_retrieve")
class VectorRetrieveStep(RetrievalStep):
    """Vector search over ChromaDB."""

    async def __call__(self, ctx: RetrievalContext) -> RetrievalContext:
        chroma = get_chroma_client()
        ctx.vector_results = chroma.search(
            ctx.query_embedding, ctx.user_id, top_k=ctx.vector_recall
        )
        return ctx


@register_step("bm25_retrieve")
class BM25RetrieveStep(RetrievalStep):
    """Keyword search over the (lazily built) per-user BM25 index."""

    async def __call__(self, ctx: RetrievalContext) -> RetrievalContext:
        if not ctx.use_hybrid:
            return ctx
        bm25 = get_bm25_service()
        # Check if user has BM25 index, if not, build it
        if not bm25.has_index(ctx.user_id):
            # Build BM25 index from SQLite for this user
            async with get_db() as db:
                async with db.execute(
                    "SELECT chunk_id, content FROM chunks WHERE user_id = ?",
                    (ctx.user_id,)
                ) as cursor:
                    rows = await cursor.fetchall()
                    if rows:
                        chunk_contents = [r["content"] for r in rows]
                        chunk_ids = [r["chunk_id"] for r in rows]
                        bm25.build_user_index(ctx.user_id, chunk_contents, chunk_ids)

        ctx.bm25_results = bm25.search(
            ctx.search_query or ctx.query, ctx.user_id, top_k=ctx.bm25_recall
        )
        return ctx


@register_step("graph_retrieve")
class GraphRetrieveStep(RetrievalStep):
    """Hard-filtered candidate set from chunks that MENTION query entities.

    Extracts entities from the query, asks the graph which chunks talk
    about them, and hydrates those chunks from Chroma. Never breaks the
    primary path: any failure falls back silently (the hybrid results
    stand alone).
    """

    async def __call__(self, ctx: RetrievalContext) -> RetrievalContext:
        if not ctx.use_graph_rag:
            return ctx
        try:
            # QueryRewriteStep may already have extracted entities in its
            # combined LLM call — reuse them to avoid a second round-trip.
            entity_names = [
                e["name"] for e in (ctx.query_entities or []) if e.get("name")
            ]
            if not entity_names:
                query_processor = await get_query_processor()
                extracted = await query_processor.extract_entities(
                    ctx.search_query or ctx.query
                )
                entity_names = [e["name"] for e in (extracted or []) if e.get("name")]
            if entity_names:
                neo4j = await get_neo4j_client()
                graph_chunk_ids = await neo4j.get_chunks_for_entities(
                    entity_names=entity_names,
                    user_id=ctx.user_id,
                    limit=max(ctx.top_k * 4, 20),
                )
                if graph_chunk_ids:
                    chroma = get_chroma_client()
                    ctx.graph_results = chroma.get_chunks_by_ids(
                        graph_chunk_ids, ctx.user_id
                    )
                    logger.info(
                        "graph_rag: extracted %d entities (%s) → %d graph chunks",
                        len(entity_names), entity_names[:5], len(ctx.graph_results),
                    )
        except Exception as e:
            # Graph-RAG is an optimization; never let it break the
            # primary retrieval path.
            logger.warning("graph_rag: extraction failed, falling back: %s", e)
        return ctx


@register_step("rrf_fuse")
class RRFFusionStep(RetrievalStep):
    """Fuse the retrieval lanes via Reciprocal Rank Fusion.

    Graph candidate chunks (when present) enter RRF as a THIRD lane by
    default — competing fairly with vector and BM25 instead of being
    force-ranked ahead. GRAPH_RRF_LANE=False restores the legacy prepend
    behavior (graph hits first, hybrid deduped) as an operator rollback.
    """

    async def __call__(self, ctx: RetrievalContext) -> RetrievalContext:
        if not ctx.use_hybrid:
            return ctx
        # Recall budget per retriever. RRF + the reranker only need enough
        # candidates to reliably contain the final top_k; RERANK_RECALL_K
        # roughly halves the rerank payload (and its latency) vs 50.
        use_lane = bool(ctx.graph_results) and getattr(
            ctx.settings, "GRAPH_RRF_LANE", True
        )
        fused_results = reciprocal_rank_fusion(
            ctx.vector_results,
            ctx.bm25_results,
            graph_results=ctx.graph_results if use_lane else None,
            k=60,
            top_k=ctx.bm25_recall,
        )
        if ctx.graph_results and not use_lane:
            # Legacy merge: graph hits first (synthetic rank boost; the
            # reranker re-scores from scratch below), hybrid deduped.
            seen = {c.get("chunk_id") for c in ctx.graph_results}
            fused_results = list(ctx.graph_results) + [
                c for c in fused_results if c.get("chunk_id") not in seen
            ]
        ctx.fused = fused_results
        return ctx


@register_step("rerank")
class RerankStep(RetrievalStep):
    """Rerank the current candidate list to the final top_k.

    Uses the fused list when the hybrid path ran (chat), else the raw
    vector results (search / vector-only path).
    """

    async def __call__(self, ctx: RetrievalContext) -> RetrievalContext:
        source = ctx.fused if ctx.fused is not None else ctx.vector_results
        if not source:
            ctx.reranked_chunks = []
            return ctx
        rerank_service = await get_rerank_service()
        ctx.reranked_chunks = await rerank_service.rerank(
            ctx.search_query or ctx.query, source, top_k=ctx.top_k
        )
        return ctx


@register_step("image_promote")
class ImagePromoteStep(RetrievalStep):
    """Cross-modal promotion, two tiers (search path):

      sim >= IMAGE_PROMOTION_THRESHOLD — the multimodal embedding
      model itself says the image is semantically close to the query
      (it shares the vector space with the query). The text-only
      reranker cannot see images, and its scores live on a compressed
      ~0.00x scale, so an unrelated text can outrank a genuinely
      relevant image. Such images are promoted AHEAD of the text
      block, best (highest sim) first.
      sim < threshold — no proven relevance; keep the image inside
      the top_k window at the last visible slot instead of burying
      it past the cutoff (text-first ordering preserved).
    """

    async def __call__(self, ctx: RetrievalContext) -> RetrievalContext:
        chunks = ctx.reranked_chunks
        images = [
            c for c in chunks
            if (c.get("metadata") or {}).get("modality") == "image"
        ]
        if images and not any(
            (c.get("metadata") or {}).get("modality") == "image"
            for c in chunks[: ctx.top_k]
        ):
            def _sim(chunk: Dict[str, Any]) -> float:
                dist = chunk.get("distance")
                return 1.0 - dist if isinstance(dist, (int, float)) else 0.0

            threshold = ctx.settings.IMAGE_PROMOTION_THRESHOLD
            hot = sorted(
                (c for c in images if _sim(c) >= threshold),
                key=_sim, reverse=True,
            )
            cold = [c for c in images if _sim(c) < threshold]
            if hot:
                chunks = hot + chunks
            if cold:
                # len(chunks) - len(images) == len(texts); with a text-heavy
                # window this equals top_k - 1 (insert at the last visible slot).
                insert_at = min(ctx.top_k - 1, max(0, len(chunks) - len(images)))
                chunks = chunks[:insert_at] + cold[:1] + chunks[insert_at:]
        ctx.reranked_chunks = chunks
        return ctx


@register_step("context_enrich")
class ContextEnrichStep(RetrievalStep):
    """Expand each reranked chunk with its prev/next neighbours.

    Search applies a two-phase, order-preserving dedup (the reranked
    list goes in FIRST — it carries the relevance_score — then only
    neighbours NOT already present are appended; a single-pass loop
    would let a scoreless context copy of a reranked chunk win the
    seen-check and silently drop the reranked version's score).
    Chat appends neighbours as-is (historical behavior).
    """

    async def __call__(self, ctx: RetrievalContext) -> RetrievalContext:
        chroma = get_chroma_client()
        chunks = ctx.reranked_chunks
        if ctx.include_context and chunks:
            if ctx.dedup_context:
                expanded_chunks: List[Dict[str, Any]] = []
                seen_chunk_ids: set = set()
                for chunk in chunks:
                    chunk_id = chunk["chunk_id"]
                    if chunk_id not in seen_chunk_ids:
                        seen_chunk_ids.add(chunk_id)
                        expanded_chunks.append(chunk)
                for chunk in chunks:
                    for cctx in chroma.get_chunk_context(
                        chunk["chunk_id"], ctx.user_id, window_size=1
                    ):
                        cctx_id = cctx["chunk_id"]
                        if cctx_id not in seen_chunk_ids:
                            seen_chunk_ids.add(cctx_id)
                            expanded_chunks.append(cctx)
                chunks = expanded_chunks
            else:
                all_chunks: List[Dict[str, Any]] = []
                for chunk in chunks:
                    all_chunks.append(chunk)
                    all_chunks.extend(
                        chroma.get_chunk_context(
                            chunk["chunk_id"], ctx.user_id, window_size=1
                        )
                    )
                chunks = all_chunks
        ctx.chunks = chunks
        return ctx


@register_step("entity_enrich")
class EntityEnrichStep(RetrievalStep):
    """Attach the entities/relations that the final chunks mention.

    Chat also appends the related-entity neighbours as "Related"-typed
    entries; search only fills relations when at least two entities were
    found. Knobs (name cap, depth, append) come from the context.
    """

    async def __call__(self, ctx: RetrievalContext) -> RetrievalContext:
        neo4j = await get_neo4j_client()
        chunk_ids = [c["chunk_id"] for c in ctx.chunks]
        entities = await neo4j.get_entities_from_chunks(chunk_ids, ctx.user_id)

        entity_names = [e["name"] for e in entities]
        relations: List[Dict[str, Any]] = []
        if ctx.append_related:
            if entity_names:
                graph_data = await neo4j.get_related_entities(
                    entity_names[: ctx.entity_name_limit],
                    ctx.user_id,
                    depth=ctx.entity_depth,
                )
                relations = graph_data.get("relations", [])
                # Add related entities to list
                for rel in relations:
                    if not any(e["name"] == rel["source"] for e in entities):
                        entities.append({"name": rel["source"], "type": "Related"})
                    if not any(e["name"] == rel["target"] for e in entities):
                        entities.append({"name": rel["target"], "type": "Related"})
        else:
            if len(entity_names) >= 2:
                graph_data = await neo4j.get_related_entities(
                    entity_names[: ctx.entity_name_limit],
                    ctx.user_id,
                    depth=ctx.entity_depth,
                )
                relations = graph_data.get("relations", [])

        ctx.entities = entities
        ctx.relations = relations
        return ctx
