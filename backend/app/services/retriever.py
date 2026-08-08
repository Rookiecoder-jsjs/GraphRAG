"""Unified retrieval pipeline shared by /api/chat and /api/search.

Consolidates the retrieval orchestration that previously lived inline in
``chat.build_rag_context`` and (in a simpler, vector-only form) in
``search.py``. Both endpoints now call :func:`retrieve`.

Pipeline (see plans/retrieval-architecture-refactor.md):
  1. Parallel LLM preprocess: conversational rewrite + multi-query variants
     (+ graph entity extraction when use_graph_rag).
  2. Embed every query (cached per text).
  3. Per-query vector + BM25 retrieval  -> multi-query recall.
  4. Graph channel as an additional RRF list.
  5. Multi-list RRF fusion (graph weight configurable).
  6. Rerank -> seed chunks.
  7. Expand: prev/next neighbours (Chroma) + parent-section siblings (SQLite),
     dedup.
  8. Re-rerank the expanded set (relevance-ordered, no neighbour eviction).
  9. Entity / relation enrichment.
Result is cached by (user, query+history, top_k, graph) for a TTL.
"""
import asyncio
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from app.config import get_settings
from app.database import get_db
from app.services.bm25 import get_bm25_service
from app.services.chroma_client import get_chroma_client
from app.services.embedding import get_embedding_service
from app.services.fusion import reciprocal_rank_fusion_multi
from app.services.neo4j_client import get_neo4j_client
from app.services.query_processor import get_query_processor
from app.services.reranker import get_rerank_service

logger = logging.getLogger(__name__)

# Mirror chat.py's _MAX_CITATION_CHUNKS: the expanded set is capped so the
# citation builder gets a focused, relevance-ordered context.
_MAX_CITATION_CHUNKS = 8

# The chunker splits oversized sections into sub-chunks whose hierarchy_path
# is "<section path>,[Part N]". Stripping that suffix yields a stable section
# key used to group siblings for parent-document expansion.
_PART_SUFFIX_RE = re.compile(r",\[Part \d+\]$")


class _RetrievalCache:
    """In-memory TTL + LRU cache for retrieval results.

    Keyed by (user_id, hash(raw_query + history), top_k, use_graph_rag) so
    the same question in a different conversation context (-> different
    conversational rewrite) is a distinct entry. Bounded to avoid unbounded
    growth on a long-running server.
    """

    def __init__(self, max_entries: int = 256):
        self._store: "OrderedDict[Tuple, Tuple[float, Dict[str, Any]]]" = OrderedDict()
        self._max = max_entries

    def get(self, key: Tuple, ttl: int) -> Optional[Dict[str, Any]]:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, val = entry
        if time.time() - ts > ttl:
            self._store.pop(key, None)
            return None
        self._store.move_to_end(key)
        return val

    def set(self, key: Tuple, val: Dict[str, Any]) -> None:
        self._store[key] = (time.time(), val)
        self._store.move_to_end(key)
        while len(self._store) > self._max:
            self._store.popitem(last=False)


_cache = _RetrievalCache()


async def _ensure_bm25_index(user_id: int) -> None:
    """Lazy-build the user's BM25 index from SQLite if missing.

    Startup prewarm (main.py) usually covers this; this is the fallback so
    retrieval never silently returns an empty BM25 channel for a user whose
    index wasn't prewarmed (brand-new user, or prewarm disabled).
    """
    bm25 = get_bm25_service()
    if bm25.has_index(user_id):
        return
    async with get_db() as db:
        async with db.execute(
            "SELECT chunk_id, content FROM chunks WHERE user_id = ? ORDER BY created_at",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
    if rows:
        bm25.build_user_index(
            user_id,
            [r["content"] for r in rows],
            [r["chunk_id"] for r in rows],
        )


async def _get_section_siblings(
    chunk_id: str, user_id: int, limit: int, max_chars: int
) -> List[Dict[str, Any]]:
    """Parent-document expansion: sibling chunks from the same SQLite section.

    Returns up to ``limit`` siblings (excluding the seed), capped by
    ``max_chars`` total, so a 500-char leaf hit can bring its whole section
    as context for the reranker. Pure SQLite (no Chroma round-trip).
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT document_id, hierarchy_path FROM chunks "
            "WHERE chunk_id = ? AND user_id = ?",
            (chunk_id, user_id),
        ) as cur:
            seed = await cur.fetchone()
        if seed is None:
            return []
        doc_id = seed["document_id"]
        section_key = _PART_SUFFIX_RE.sub("", seed["hierarchy_path"] or "")

        async with db.execute(
            "SELECT chunk_id, content, hierarchy_path FROM chunks "
            "WHERE document_id = ? AND user_id = ? ORDER BY created_at",
            (doc_id, user_id),
        ) as cur:
            rows = await cur.fetchall()

    siblings: List[Dict[str, Any]] = []
    total = 0
    for r in rows:
        if r["chunk_id"] == chunk_id:
            continue
        if _PART_SUFFIX_RE.sub("", r["hierarchy_path"] or "") != section_key:
            continue
        content = r["content"] or ""
        if total + len(content) > max_chars:
            break
        siblings.append({
            "chunk_id": r["chunk_id"],
            "content": content,
            "metadata": {
                "document_id": doc_id,
                "hierarchy_path": r["hierarchy_path"] or "",
            },
        })
        total += len(content)
        if len(siblings) >= limit:
            break
    return siblings


async def retrieve(
    query: str,
    user_id: int,
    top_k: int = 5,
    use_graph_rag: bool = False,
    conversation_history: Optional[List[Dict[str, str]]] = None,
    enable_rewrite: bool = True,
) -> Dict[str, Any]:
    """Unified retrieval. Returns ``{"chunks", "entities", "relations"}``."""
    settings = get_settings()
    t_start = time.perf_counter()

    # Graph-RAG mode: an explicit user toggle (use_graph_rag=True) always
    # forces the graph channel on. Otherwise GRAPH_RAG_MODE decides:
    #   "off" -> never; "on" -> always; "auto" -> extract entities and
    #   only run the graph channel when >=2 match the user's graph.
    graph_mode = settings.GRAPH_RAG_MODE.lower()
    _auto_graph = False
    if not use_graph_rag:
        if graph_mode == "on":
            use_graph_rag = True
        elif graph_mode == "auto":
            use_graph_rag = True
            _auto_graph = True

    # ---- Cache lookup (key includes history so context-aware rewrites differ) ----
    hist_json = ""
    if conversation_history:
        n = settings.CONVERSATIONAL_REWRITE_HISTORY_TURNS
        hist_json = json.dumps(
            conversation_history[-n:], sort_keys=True, ensure_ascii=False
        )
    cache_key = (
        user_id,
        hashlib.sha1(f"{query}|{hist_json}".encode("utf-8")).hexdigest(),
        top_k,
        use_graph_rag,
    )
    cached = _cache.get(cache_key, settings.RETRIEVAL_CACHE_TTL)
    if cached is not None:
        logger.info("retrieve: cache hit (user_id=%d)", user_id)
        return cached

    qp = await get_query_processor()
    chroma = get_chroma_client()
    bm25 = get_bm25_service()
    neo4j = await get_neo4j_client()

    # ---- 1. Parallel LLM preprocessing (#6) ----
    rewrite_task: Optional[asyncio.Task] = None
    if enable_rewrite and len(query.strip()) >= settings.QUERY_REWRITE_MIN_LEN:
        rewrite_task = asyncio.create_task(qp.rewrite_query(query, conversation_history))
    variants_task = asyncio.create_task(
        qp.generate_query_variants(query, settings.MULTI_QUERY_NUM_VARIANTS)
    )
    entities_task: Optional[asyncio.Task] = None
    if use_graph_rag:
        entities_task = asyncio.create_task(qp.extract_entities(query))

    rewritten = query
    if rewrite_task is not None:
        try:
            r = await rewrite_task
            if r and r.strip():
                rewritten = r.strip()
        except Exception as e:
            logger.warning("retrieve: rewrite failed, using raw query: %s", e)

    variants: List[str] = []
    try:
        variants = [v for v in (await variants_task) if v and v.strip()]
    except Exception as e:
        logger.warning("retrieve: variants failed: %s", e)

    query_entities: List[Dict[str, str]] = []
    if entities_task is not None:
        try:
            query_entities = await entities_task
        except Exception as e:
            logger.warning("retrieve: entity extraction failed: %s", e)

    # De-dup queries (rewritten first, then variants).
    seen_q: set = set()
    queries: List[str] = []
    for q in [rewritten] + variants:
        qs = q.strip()
        if qs and qs not in seen_q:
            seen_q.add(qs)
            queries.append(qs)
    t_rewrite = time.perf_counter()

    # ---- 2. Embed all queries in parallel (cached per text) ----
    embedding_service = await get_embedding_service()
    embeddings = await asyncio.gather(
        *[embedding_service.embed_single(q) for q in queries],
        return_exceptions=True,
    )
    query_embeddings: List[List[float]] = []
    valid_queries: List[str] = []
    for q, emb in zip(queries, embeddings):
        if isinstance(emb, Exception) or not emb:
            logger.warning("retrieve: embedding failed for %r: %s", q, emb)
            continue
        query_embeddings.append(emb)
        valid_queries.append(q)
    if not query_embeddings:
        return {"chunks": [], "entities": [], "relations": []}
    t_embed = time.perf_counter()

    # ---- 3. BM25 index (lazy fallback if prewarm didn't cover this user) ----
    await _ensure_bm25_index(user_id)

    recall_k = settings.RERANK_RECALL_K

    # ---- 4. Per-query vector + BM25 retrieval (#3 multi-query) ----
    result_lists: List[List[Dict[str, Any]]] = []
    labels: List[str] = []
    for i, (q, emb) in enumerate(zip(valid_queries, query_embeddings)):
        result_lists.append(chroma.search(emb, user_id, top_k=recall_k))
        labels.append("vector" if i == 0 else f"vector_{i}")
        result_lists.append(bm25.search(q, user_id, top_k=recall_k))
        labels.append("bm25" if i == 0 else f"bm25_{i}")

    # ---- 5. Graph channel as an RRF list (#5) ----
    graph_chunks: List[Dict[str, Any]] = []
    if use_graph_rag:
        entity_names = [e["name"] for e in (query_entities or []) if e.get("name")]
        # auto mode requires >=2 matched entities; explicit toggle / "on"
        # accept any match.
        if entity_names and (not _auto_graph or len(entity_names) >= 2):
            try:
                graph_chunk_ids = await neo4j.get_chunks_for_entities(
                    entity_names=entity_names,
                    user_id=user_id,
                    limit=max(top_k * 4, 20),
                )
                if graph_chunk_ids:
                    graph_chunks = chroma.get_chunks_by_ids(graph_chunk_ids, user_id)
                    if graph_chunks:
                        result_lists.append(graph_chunks)
                        labels.append("graph")
                        logger.info(
                            "retrieve: graph channel %d entities -> %d chunks",
                            len(entity_names), len(graph_chunks),
                        )
            except Exception as e:
                logger.warning("retrieve: graph-RAG failed, skipping channel: %s", e)

    # ---- 6. Multi-list RRF fusion (#3/#5) ----
    weights = [1.0] * len(result_lists)
    if graph_chunks and settings.GRAPH_RRF_WEIGHT != 1.0:
        for idx, lab in enumerate(labels):
            if lab == "graph":
                weights[idx] = settings.GRAPH_RRF_WEIGHT
    fused = reciprocal_rank_fusion_multi(
        result_lists, k=60, top_k=recall_k, weights=weights, labels=labels,
    )
    t_retrieve = time.perf_counter()

    if not fused:
        result = {"chunks": [], "entities": [], "relations": []}
        _cache.set(cache_key, result)
        return result

    # ---- 7. Rerank -> seeds ----
    rerank_service = await get_rerank_service()
    seeds = await rerank_service.rerank(rewritten, fused, top_k=top_k)
    t_rerank = time.perf_counter()

    # ---- 8. Expand: neighbours (#1) + section siblings (#4), dedup ----
    expanded: List[Dict[str, Any]] = []
    seen_ids: set = set()

    def _add(chunk: Dict[str, Any]) -> None:
        cid = chunk.get("chunk_id") or chunk.get("id")
        if cid and cid not in seen_ids:
            seen_ids.add(cid)
            chunk.setdefault("chunk_id", cid)
            expanded.append(chunk)

    for seed in seeds:
        _add(seed)
        cid = seed.get("chunk_id") or seed.get("id")
        if not cid:
            continue
        try:
            for nb in chroma.get_chunk_context(cid, user_id, window_size=1):
                _add(nb)
        except Exception as e:
            logger.warning("retrieve: neighbour expand failed for %s: %s", cid, e)
        try:
            for sb in await _get_section_siblings(
                cid, user_id,
                limit=settings.PARENT_SECTION_SIBLING_LIMIT,
                max_chars=settings.PARENT_SECTION_MAX_CHARS,
            ):
                _add(sb)
        except Exception as e:
            logger.warning("retrieve: section expand failed for %s: %s", cid, e)

    # ---- 9. Re-rerank the expanded set (#1: relevance-ordered, no eviction) ----
    if settings.ENABLE_EXPANSION_RERERANK and len(expanded) > 1:
        try:
            expanded = await rerank_service.rerank(
                rewritten, expanded, top_k=len(expanded)
            )
        except Exception as e:
            logger.warning("retrieve: expansion rererank failed, keeping order: %s", e)
    expanded = expanded[: max(_MAX_CITATION_CHUNKS, top_k)]
    t_expand = time.perf_counter()

    # ---- 10. Entity / relation enrichment ----
    chunk_ids = [c.get("chunk_id") for c in expanded if c.get("chunk_id")]
    entities = await neo4j.get_entities_from_chunks(chunk_ids, user_id) if chunk_ids else []
    relations: List[Dict[str, Any]] = []
    entity_names = [e["name"] for e in entities if e.get("name")]
    if entity_names:
        graph_data = await neo4j.get_related_entities(entity_names[:3], user_id, depth=2)
        relations = graph_data.get("relations", [])
        for rel in relations:
            for key in ("source", "target"):
                if not any(e["name"] == rel.get(key) for e in entities):
                    entities.append({"name": rel.get(key), "type": "Related"})

    t_end = time.perf_counter()
    logger.info(
        "retrieve timing: rewrite=%.3fs embed=%.3fs retrieve=%.3fs rerank=%.3fs "
        "expand=%.3fs enrich=%.3fs total=%.3fs (queries=%d, seeds=%d, expanded=%d)",
        t_rewrite - t_start, t_embed - t_rewrite, t_retrieve - t_embed,
        t_rerank - t_retrieve, t_expand - t_rerank, t_end - t_expand, t_end - t_start,
        len(valid_queries), len(seeds), len(expanded),
    )

    result = {"chunks": expanded, "entities": entities, "relations": relations}
    _cache.set(cache_key, result)
    return result
