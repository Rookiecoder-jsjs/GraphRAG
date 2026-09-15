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
import copy
import hashlib
import json
import logging
import re
import time
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.config import get_settings
from app.database import get_db
from app.services import graph_community
from app.services.bm25 import get_bm25_service
from app.services.entity_alias import resolve_names
from app.services.chroma_client import get_chroma_client
from app.services.embedding import EmbeddingServiceError, get_embedding_service
from app.services.fusion import reciprocal_rank_fusion_multi
from app.services.neo4j_client import get_neo4j_client
from app.services.query_gate import get_query_gate
from app.services.query_processor import get_query_processor
from app.services.reranker import get_rerank_service
from app.services.retrieval.debug import DebugCollector

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

    Keyed by (user_id, hash(raw_query + history), top_k, use_graph_rag,
    filter_key, behavior_fingerprint) so the same question in a different
    conversation context (-> different conversational rewrite) is a distinct
    entry, AND a config change that alters retrieval behavior (fusion weights,
    recall depth, variant count, model) does not serve stale results for the
    rest of the TTL. Bounded to avoid unbounded growth on a long-running
    server.
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
        # Deep-copy on the way out: the caller mutates the result (e.g. the
        # expansion step setdefaults chunk_id). Returning the shared object
        # by reference would leak one caller's mutations into every later
        # cache hit.
        return copy.deepcopy(val)

    def set(self, key: Tuple, val: Dict[str, Any]) -> None:
        self._store[key] = (time.time(), val)
        self._store.move_to_end(key)
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    def invalidate_user(self, user_id: int) -> None:
        """Drop every cached entry for a user.

        Called on document upload/delete: the corpus that user queries
        against changed, and TTL alone can't cover writes — a deleted
        document's chunks would otherwise keep being returned for up to a
        full TTL. Keys are ``(user_id, hash, top_k, graph)`` so we can
        filter by the first element.
        """
        for key in [k for k in self._store if k[0] == user_id]:
            self._store.pop(key, None)


def _behavior_fingerprint(settings) -> str:
    """sha1 over every config knob that changes WHAT retrieval returns.

    Rides in the cache key so an ops-side tweak (fusion weight, recall
    depth, variant count, model swap, graph mode) takes effect immediately
    instead of after the TTL.
    """
    behavior = "|".join([
        str(settings.GRAPH_RRF_WEIGHT),
        str(settings.COMMUNITY_RRF_WEIGHT),
        str(settings.RERANK_RECALL_K),
        str(settings.MULTI_QUERY_NUM_VARIANTS),
        settings.EMBEDDING_MODEL,
        settings.RERANK_MODEL,
        settings.GRAPH_RAG_MODE,
    ])
    return hashlib.sha1(behavior.encode("utf-8")).hexdigest()


_cache = _RetrievalCache()


def invalidate_retrieval_cache(user_id: int) -> None:
    """Invalidate cached retrieval results for one user.

    Invoked from the document lifecycle (upload completion / delete /
    failure cleanup) so ``retrieve()`` never serves results that reference
    chunks the user has since added or removed.
    """
    _cache.invalidate_user(user_id)


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
            "SELECT chunk_id, document_id, content FROM chunks WHERE user_id = ? "
            "ORDER BY created_at, chunk_id",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
    if rows:
        # jieba tokenisation + BM25 build is CPU-heavy; run it off the event
        # loop so one cold user doesn't stall every concurrent request.
        await asyncio.to_thread(
            bm25.build_user_index,
            user_id,
            [r["content"] for r in rows],
            [r["chunk_id"] for r in rows],
            [r["document_id"] for r in rows],
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
            "WHERE document_id = ? AND user_id = ? "
            "ORDER BY created_at, chunk_id",
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
    debug: bool = False,
    document_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Unified retrieval. Returns ``{"chunks", "entities", "relations"}``.

    Thin wrapper: resolves the graph-RAG mode, consults the retrieval cache,
    then delegates the full pipeline to ``_retrieve_uncached``. The split
    keeps the cache-hit path free of any per-request setup the pipeline only
    needs on a miss (and lets the admission gate wrap exactly the miss path).

    ``debug=True`` (FEAT-024, /api/search/debug): bypasses the cache in BOTH
    directions — no read (the caller wants a real run, not yesterday's) and
    no write (the debug payload must never leak into entries that regular
    requests will share by reference). Timing and stage snapshots are
    therefore always fresh, at full pipeline cost.

    ``document_ids`` (FEAT-026) scopes every channel to that document
    subset: None = whole library. An empty/blank-only list resolves to an
    empty result without running the (billable) pipeline — callers that
    resolve a tag first (api/search.py, api/chat.py) short-circuit even
    earlier, but direct callers stay safe: chroma's ``$in: []`` throws.
    """
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

    # Empty scope short-circuit: nothing is in scope, so there is nothing
    # to retrieve. Runs before the admission gate — a hopeless request must
    # not consume a concurrency slot.
    if document_ids is not None and not [d for d in document_ids if d]:
        return {"chunks": [], "entities": [], "relations": []}

    # ---- Cache lookup (key includes history so context-aware rewrites differ) ----
    hist_json = ""
    if conversation_history:
        n = settings.CONVERSATIONAL_REWRITE_HISTORY_TURNS
        hist_json = json.dumps(
            conversation_history[-n:], sort_keys=True, ensure_ascii=False
        )
    # 5th key element: None (unfiltered) vs the sha1 of the sorted id set.
    # None and [] must NEVER collide — [] means "nothing allowed".
    filter_key: Optional[str] = None
    if document_ids is not None:
        filter_key = hashlib.sha1(
            ",".join(sorted({d for d in document_ids if d})).encode("utf-8")
        ).hexdigest()
    cache_key = (
        user_id,
        hashlib.sha1(f"{query}|{hist_json}".encode("utf-8")).hexdigest(),
        top_k,
        use_graph_rag,
        filter_key,
        _behavior_fingerprint(settings),
    )
    cached = None if debug else _cache.get(cache_key, settings.RETRIEVAL_CACHE_TTL)
    if cached is not None:
        logger.info("retrieve: cache hit (user_id=%d)", user_id)
        return cached

    # Admission gate (ADR-009): cache misses queue here — at most
    # QUERY_CONCURRENCY full pipelines run at once, excess waits up to
    # QUERY_MAX_QUEUE_SECONDS then QueryGateTimeout escapes to the caller
    # (search → 429 + Retry-After, chat → terminal busy SSE error). Cache
    # hits above never consume capacity. Rejection beats degradation: an
    # admitted retrieval always runs the full-quality path.
    #
    # Debug runs (FEAT-024) consume a slot like any other miss — their
    # timings must reflect real admission — but pass cache_key=None so the
    # pipeline skips both cache writes (see _retrieve_uncached).
    t_gate = time.perf_counter()
    async with get_query_gate().slot():
        return await _retrieve_uncached(
            query=query,
            user_id=user_id,
            top_k=top_k,
            use_graph_rag=use_graph_rag,
            _auto_graph=_auto_graph,
            conversation_history=conversation_history,
            enable_rewrite=enable_rewrite,
            cache_key=None if debug else cache_key,
            t_start=t_start,
            t_gate=t_gate,
            debug_out=DebugCollector() if debug else None,
            document_ids=list(document_ids) if document_ids is not None else None,
        )


async def _retrieve_uncached(
    query: str,
    user_id: int,
    top_k: int,
    use_graph_rag: bool,
    _auto_graph: bool,
    conversation_history: Optional[List[Dict[str, str]]],
    enable_rewrite: bool,
    cache_key: Optional[Tuple[int, str, int, bool, Optional[str]]],
    t_start: float,
    t_gate: float,
    debug_out: Optional[DebugCollector] = None,
    document_ids: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Full retrieval pipeline, executed only on a retrieval-cache miss.

    The caller holds a query-gate admission slot for the whole body.

    Degradation tracking: every optional channel that fails and falls back
    appends a label to ``degraded``; the final timing log carries the list
    (the JSON formatter merges ``extra`` top-level) so load-induced quality
    loss is observable instead of silent. The list is log-only — the cached
    result object is shared by reference across cache hits and must not
    carry per-request state.
    """
    settings = get_settings()
    degraded: List[str] = []
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
            degraded.append("rewrite_failed")
            logger.warning("retrieve: rewrite failed, using raw query: %s", e)

    variants: List[str] = []
    try:
        variants = [v for v in (await variants_task) if v and v.strip()]
    except Exception as e:
        degraded.append("variants_failed")
        logger.warning("retrieve: variants failed: %s", e)

    query_entities: List[Dict[str, str]] = []
    if entities_task is not None:
        try:
            query_entities = await entities_task
        except Exception as e:
            degraded.append("entities_failed")
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

    if debug_out is not None:
        debug_out.stage("rewrite", {
            "raw_query": query,
            "rewritten": rewritten,
            "rewrite_applied": rewritten != query,
            "variants": variants,
            "final_queries": queries,
            "query_entities": query_entities,
        })

    # ---- 2. Embed all queries in one batched request (cache-aware) ----
    # The deduped query list (rewrite + variants) is <=1+MULTI_QUERY_NUM_
    # VARIANTS texts, so embed_batch sends a single /embeddings request —
    # the per-variant gather used to fire 1+N separate calls and each failed
    # variant was silently dropped (a load-induced recall loss). All-or-
    # nothing here is deliberate: total failure yields the same empty-result
    # outcome as before, success yields every variant's vector.
    embedding_service = await get_embedding_service()
    try:
        query_embeddings = await embedding_service.embed_batch(queries)
        valid_queries = list(queries)
    except EmbeddingServiceError as e:
        degraded.append("embed_failed")
        logger.warning(
            "retrieve: query embedding failed (%d queries), returning empty "
            "results (degraded=%s): %s", len(queries), degraded, e,
        )
        result: Dict[str, Any] = {"chunks": [], "entities": [], "relations": []}
        if debug_out is not None:
            debug_out.stage("diagnostics", {
                "degraded": list(degraded),
                "timing_s": {"total": round(time.perf_counter() - t_start, 3)},
            })
            result = {**result, "debug": debug_out.finish()}
        return result
    t_embed = time.perf_counter()

    # ---- 3. BM25 index (lazy fallback if prewarm didn't cover this user) ----
    await _ensure_bm25_index(user_id)

    recall_k = settings.RERANK_RECALL_K

    # ---- 4. Per-query vector + BM25 retrieval (#3 multi-query) ----
    # The chromadb HttpClient is synchronous and BM25 scoring is CPU-bound,
    # so both go through asyncio.to_thread: calling them inline would block
    # the event loop for the duration of every HTTP round-trip / scoring
    # pass, silently serialising all concurrent requests behind one
    # retrieval. Dispatch every channel first, then await them together.
    result_lists: List[List[Dict[str, Any]]] = []
    labels: List[str] = []
    recall_tasks = []
    for q, emb in zip(valid_queries, query_embeddings):
        recall_tasks.append(
            asyncio.to_thread(chroma.search, emb, user_id, recall_k,
                              document_ids=document_ids)
        )
        recall_tasks.append(
            asyncio.to_thread(bm25.search, q, user_id, recall_k,
                              document_ids=document_ids)
        )
    recall_results = await asyncio.gather(*recall_tasks)
    for i in range(len(valid_queries)):
        result_lists.append(recall_results[2 * i])
        labels.append("vector" if i == 0 else f"vector_{i}")
        result_lists.append(recall_results[2 * i + 1])
        labels.append("bm25" if i == 0 else f"bm25_{i}")

    # ---- 5. Graph channel as an RRF list (#5) ----
    graph_chunks: List[Dict[str, Any]] = []
    if use_graph_rag:
        entity_names = [e["name"] for e in (query_entities or []) if e.get("name")]
        # FEAT-025: a query mentioning a merged-away name must still reach
        # the canonical node — resolve aliases before the exact-name lookup.
        if entity_names:
            try:
                entity_names = await resolve_names(entity_names, user_id)
            except Exception as e:
                degraded.append("alias_resolve_failed")
                logger.warning("retrieve: alias resolution failed: %s", e)
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
                    graph_chunks = await asyncio.to_thread(
                        chroma.get_chunks_by_ids, graph_chunk_ids, user_id
                    )
                    if graph_chunks and document_ids is not None:
                        # FEAT-026: chunk nodes carry no document_id in Neo4j,
                        # but the Chroma metadata does — filter the fan-out
                        # result so the graph channel cannot break the scope.
                        allowed = set(document_ids)
                        graph_chunks = [
                            c for c in graph_chunks
                            if (c.get("metadata") or {}).get("document_id") in allowed
                        ]
                    if graph_chunks:
                        result_lists.append(graph_chunks)
                        labels.append("graph")
                        logger.info(
                            "retrieve: graph channel %d entities -> %d chunks",
                            len(entity_names), len(graph_chunks),
                        )
            except Exception as e:
                degraded.append("graph_skipped")
                logger.warning("retrieve: graph-RAG failed, skipping channel: %s", e)

    # ---- 5b. Community channel (FEAT-028 global search, ADR-010) ----
    # Same use_graph_rag gate as the entity channel; costs one indexed
    # SQLite existence check when no communities were ever built.
    community_chunks: List[Dict[str, Any]] = []
    if use_graph_rag:
        try:
            if await graph_community.has_communities(user_id):
                community_chunks = await graph_community.fetch_community_chunks(
                    user_id, query_embeddings[0], max(top_k * 4, 20),
                    document_ids=document_ids,
                )
                if community_chunks:
                    result_lists.append(community_chunks)
                    labels.append("community")
                    logger.info(
                        "retrieve: community channel -> %d chunks",
                        len(community_chunks),
                    )
        except Exception as e:
            degraded.append("community_skipped")
            logger.warning("retrieve: community channel failed: %s", e)

    # ---- 6. Multi-list RRF fusion (#3/#5) ----
    if debug_out is not None:
        for _lab, _lst in zip(labels, result_lists):
            _kind = (
                "graph" if _lab == "graph"
                else "community" if _lab == "community"
                else "vector" if _lab.startswith("vector")
                else "bm25"
            )
            debug_out.channel(_lab, _kind, _lst)

    weights = [1.0] * len(result_lists)
    if graph_chunks and settings.GRAPH_RRF_WEIGHT != 1.0:
        for idx, lab in enumerate(labels):
            if lab == "graph":
                weights[idx] = settings.GRAPH_RRF_WEIGHT
    if community_chunks and settings.COMMUNITY_RRF_WEIGHT != 1.0:
        for idx, lab in enumerate(labels):
            if lab == "community":
                weights[idx] = settings.COMMUNITY_RRF_WEIGHT
    fused = reciprocal_rank_fusion_multi(
        result_lists, k=60, top_k=recall_k, weights=weights, labels=labels,
    )
    t_retrieve = time.perf_counter()

    if debug_out is not None:
        debug_out.stage("fused", [debug_out.snap(f) for f in fused])

    if not fused:
        result = {"chunks": [], "entities": [], "relations": []}
        if cache_key is not None:
            _cache.set(cache_key, result)
        if debug_out is not None:
            result = {**result, "debug": debug_out.finish()}
        return result

    # ---- 7. Rerank -> seeds ----
    rerank_service = await get_rerank_service()
    try:
        seeds = await rerank_service.rerank(rewritten, fused, top_k=top_k)
    except Exception as e:
        # Rerank is an optimisation, not a requirement: on any failure (HTTP
        # error, unexpected payload shape, timeout) fall back to the fused
        # RRF order instead of failing the whole chat/search request.
        degraded.append("rerank_fallback")
        logger.warning("retrieve: rerank failed, falling back to RRF order: %s", e)
        seeds = fused[:top_k]
    t_rerank = time.perf_counter()

    if debug_out is not None:
        debug_out.stage("seeds", [debug_out.snap(s) for s in seeds])

    # ---- 8. Expand: neighbours (#1) + section siblings (#4), dedup ----
    expanded: List[Dict[str, Any]] = []
    seen_ids: set = set()
    # provenance per chunk id (seed / neighbour / sibling) — kept in a side
    # map rather than annotated onto the chunk dicts, which are shared with
    # the caller (and would otherwise leak debug state into the result).
    provenance: Dict[str, str] = {}

    def _add(chunk: Dict[str, Any], kind: str) -> None:
        cid = chunk.get("chunk_id") or chunk.get("id")
        if cid and cid not in seen_ids:
            seen_ids.add(cid)
            provenance[cid] = kind
            chunk.setdefault("chunk_id", cid)
            expanded.append(chunk)

    for seed in seeds:
        _add(seed, "seed")
        cid = seed.get("chunk_id") or seed.get("id")
        if not cid:
            continue
        try:
            neighbours = await asyncio.to_thread(
                chroma.get_chunk_context, cid, user_id, 1
            )
            for nb in neighbours:
                _add(nb, "neighbour")
        except Exception as e:
            degraded.append("expand_neighbour_failed")
            logger.warning("retrieve: neighbour expand failed for %s: %s", cid, e)
        try:
            for sb in await _get_section_siblings(
                cid, user_id,
                limit=settings.PARENT_SECTION_SIBLING_LIMIT,
                max_chars=settings.PARENT_SECTION_MAX_CHARS,
            ):
                _add(sb, "sibling")
        except Exception as e:
            degraded.append("expand_section_failed")
            logger.warning("retrieve: section expand failed for %s: %s", cid, e)

    # ---- 9. Re-rerank the expanded set (#1: relevance-ordered, no eviction) ----
    if settings.ENABLE_EXPANSION_RERERANK and len(expanded) > 1:
        try:
            expanded = await rerank_service.rerank(
                rewritten, expanded, top_k=len(expanded)
            )
        except Exception as e:
            degraded.append("rerank_fallback_expansion")
            logger.warning("retrieve: expansion rererank failed, keeping order: %s", e)
    expanded = expanded[: max(_MAX_CITATION_CHUNKS, top_k)]
    t_expand = time.perf_counter()

    if debug_out is not None:
        debug_out.stage("expanded", [
            debug_out.snap(
                c,
                provenance=provenance.get(c.get("chunk_id"), "unknown"),
            )
            for c in expanded
        ])

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
    extra: Dict[str, Any] = {
        "timing_s": {
            "queue": round(t_gate - t_start, 3),
            "rewrite": round(t_rewrite - t_start, 3),
            "embed": round(t_embed - t_rewrite, 3),
            "retrieve": round(t_retrieve - t_embed, 3),
            "rerank": round(t_rerank - t_retrieve, 3),
            "expand": round(t_expand - t_rerank, 3),
            "enrich": round(t_end - t_expand, 3),
            "total": round(t_end - t_start, 3),
        },
        "queries": len(valid_queries),
        "seeds": len(seeds),
        "expanded": len(expanded),
    }
    if degraded:
        # Structured degradation report — the JSON formatter merges extra
        # keys top-level; text mode ignores them. Log-only by design: the
        # cached result is shared by reference and must stay per-request clean.
        extra["degraded"] = degraded
    logger.info(
        "retrieve timing: rewrite=%.3fs embed=%.3fs retrieve=%.3fs rerank=%.3fs "
        "expand=%.3fs enrich=%.3fs total=%.3fs (queries=%d, seeds=%d, expanded=%d)",
        t_rewrite - t_start, t_embed - t_rewrite, t_retrieve - t_embed,
        t_rerank - t_retrieve, t_expand - t_rerank, t_end - t_expand, t_end - t_start,
        len(valid_queries), len(seeds), len(expanded),
        extra=extra,
    )

    # Callers must not mutate this dict: it is handed to cache hits by
    # reference until the TTL expires (chat citation builder and /api/search
    # are read-only today). Debug runs (cache_key=None, FEAT-024) never enter
    # the cache; their debug payload is merged AFTER the cache guard so it
    # can only ever reach the one caller that asked for it.
    if debug_out is not None:
        debug_out.stage("diagnostics", {
            "degraded": list(degraded),
            "timing_s": extra["timing_s"],
        })
        debug_out.stage("config", {
            "top_k": top_k,
            "use_graph_rag": use_graph_rag,
            "graph_mode": settings.GRAPH_RAG_MODE.lower(),
            "recall_k": recall_k,
            "queries": len(valid_queries),
            "document_filter": (
                sorted(set(document_ids)) if document_ids is not None else None
            ),
        })
    result = {"chunks": expanded, "entities": entities, "relations": relations}
    if cache_key is not None:
        _cache.set(cache_key, result)
    if debug_out is not None:
        result = {**result, "debug": debug_out.finish()}
    return result
