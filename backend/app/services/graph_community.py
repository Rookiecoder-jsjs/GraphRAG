"""Graph community detection + global-search channel (FEAT-028, ADR-010).

Offline (rebuild) side: partition the user's entity graph into communities
(networkx greedy modularity over RELATES_TO edges — deterministic, no RNG),
summarize each community with ONE LLM call, embed the summaries, and store
them twice: SQLite rows (the /graph/global page + staleness math) and
vectors in the ``community_summaries`` Chroma collection (query-time
lookup).

Online (retrieval) side: ``fetch_community_chunks`` maps the query to the
top communities by summary similarity, then returns the CHUNKS mentioning
their member entities — real, citable chunks that ride the normal RRF →
rerank → citation pipeline. Design decision (ADR-010): summaries choose
WHICH chunks enter the context; they are never injected into the RAG
prompt themselves, so the generation prompt (and its eval metrics) stay
untouched.

SQL lives in this module; graph reads go through Neo4jClient. All LLM
failures degrade to a generic "主题 N" title with the member list as
summary — a rebuild never fails because the summarizer sneezed.
"""
import asyncio
import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence

from app.config import get_settings
from app.database import get_db
from app.prompts import load_prompt
from app.services.chroma_client import get_chroma_client
from app.services.embedding import get_embedding_service
from app.services.llm import get_llm_service
from app.services.neo4j_client import get_neo4j_client

logger = logging.getLogger(__name__)


# =========================================================================
# Pure grouping (no I/O — directly unit-testable)
# =========================================================================

def group_entities(
    edges: Sequence[Dict[str, Any]],
    mention_by_name: Dict[str, int],
    *,
    min_size: int,
    max_communities: int,
) -> List[Dict[str, Any]]:
    """Partition entities into communities over RELATES_TO edges.

    Deterministic end-to-end: greedy modularity (no RNG), members sorted,
    output ranked by total mention_count desc then member list. Communities
    smaller than ``min_size`` are dropped; output capped at
    ``max_communities``. Entities with no RELATES_TO edges never appear.
    """
    import networkx as nx  # lazy: only a rebuild pays the import

    graph = nx.Graph()
    for edge in edges:
        src, tgt = edge.get("source"), edge.get("target")
        if not src or not tgt or src == tgt:
            continue
        weight = graph[src][tgt]["weight"] + 1 if graph.has_edge(src, tgt) else 1
        graph.add_edge(src, tgt, weight=weight)

    groups: List[Dict[str, Any]] = []
    for community in nx.community.greedy_modularity_communities(graph, weight="weight"):
        members = sorted(community)
        if len(members) < min_size:
            continue
        groups.append({
            "members": members,
            "mention_total": sum(mention_by_name.get(m, 0) for m in members),
        })
    groups.sort(key=lambda g: (-g["mention_total"], g["members"]))
    return groups[:max_communities]


# =========================================================================
# Rebuild
# =========================================================================

def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Module-local JSON extraction (judge.py:92 convention — llm.py's
    helper is an instance method and not importable)."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def build_communities(user_id: int) -> Dict[str, Any]:
    """Detect, summarize, and persist communities for one user (rebuild).

    Replaces any previous rows/vectors for the user. The Chroma delete
    runs BEFORE the upsert: community vector ids are positional
    (community_{user_id}_{n}), so a shrink without the delete would leave
    stale higher-numbered vectors queryable. SQLite rows are replaced only
    after the vectors land, so a mid-rebuild failure keeps the page (and
    staleness math) coherent with the previous build.
    """
    settings = get_settings()
    neo4j = await get_neo4j_client()
    edges = await neo4j.get_user_relation_edges(user_id, limit=5000)
    entity_rows = await neo4j.get_user_entities_with_mentions(user_id=user_id, limit=10000)
    mention_by_name = {
        r["name"]: int(r.get("mention_count") or 0) for r in entity_rows
    }
    entity_count = len(entity_rows)

    groups = group_entities(
        edges, mention_by_name,
        min_size=settings.COMMUNITY_MIN_SIZE,
        max_communities=settings.COMMUNITY_MAX,
    )
    if not groups:
        await _persist(user_id, [], entity_count)
        return {"communities": 0, "entities": entity_count, "edges": len(edges)}

    summaries = await _summarize_all(user_id, groups, edges)

    chroma = get_chroma_client()
    await asyncio.to_thread(chroma.delete_user_communities, user_id)
    if summaries:
        embedding_service = await get_embedding_service()
        texts = [f"{s['title']}\n{s['summary']}" for s in summaries]
        embeddings = await embedding_service.embed_batch(texts)
        ids = [f"community_{user_id}_{i}" for i in range(len(summaries))]
        metadatas = [
            {
                "user_id": str(user_id),
                "community_index": i,
                "member_count": s["member_count"],
                "member_names": json.dumps(s["members"], ensure_ascii=False),
            }
            for i, s in enumerate(summaries)
        ]
        await asyncio.to_thread(
            chroma.add_communities, ids, texts, metadatas, embeddings,
        )
    await _persist(user_id, summaries, entity_count)

    logger.info(
        "build_communities(user_id=%d): %d communities from %d edges / %d entities",
        user_id, len(summaries), len(edges), entity_count,
    )
    return {"communities": len(summaries), "entities": entity_count, "edges": len(edges)}


async def _summarize_all(
    user_id: int, groups: List[Dict[str, Any]], edges: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """One bounded LLM call per community, fanned out under a semaphore.

    Failure degrades to a generic title + member list — the community is
    still usable for chunk retrieval, it just reads plainly on the page.
    """
    llm = await get_llm_service()
    semaphore = asyncio.Semaphore(8)
    member_set = {m for g in groups for m in g["members"]}
    relation_sample = [
        f"{e['source']} -[{e.get('relation_type') or 'RELATED'}]-> {e['target']}"
        for e in edges
        if e.get("source") in member_set and e.get("target") in member_set
    ][:15]

    async def summarize(index: int, group: Dict[str, Any]) -> Dict[str, Any]:
        members = group["members"]
        fallback_summary = "、".join(members[:10]) + ("…" if len(members) > 10 else "")
        title, summary = f"主题 {index + 1}", fallback_summary
        try:
            async with semaphore:
                prompt = load_prompt(
                    "community_summary",
                    members="、".join(members),
                    relations="\n".join(relation_sample) or "（无）",
                )
                raw = await llm.chat_complete(
                    [{"role": "user", "content": prompt}],
                    temperature=0.2,
                    max_tokens=512,
                    enable_thinking=False,
                )
            data = _extract_json_object(raw)
            if data:
                title = (str(data.get("title") or "").strip() or title)[:80]
                summary = (str(data.get("summary") or "").strip() or fallback_summary)[:600]
        except Exception as e:  # noqa: BLE001 — summarize must never fail a rebuild
            logger.warning("community summarize failed (idx=%d): %s", index, e)
        return {
            "title": title,
            "summary": summary,
            "members": members,
            "member_count": len(members),
            "mention_total": group["mention_total"],
        }

    return list(await asyncio.gather(*(
        summarize(i, g) for i, g in enumerate(groups)
    )))


async def _persist(
    user_id: int, summaries: List[Dict[str, Any]], entity_count: int
) -> None:
    """Replace the user's community rows (caller handles chroma vectors)."""
    async with get_db() as db:
        await db.execute(
            "DELETE FROM graph_communities WHERE user_id = ?", (user_id,)
        )
        for s in summaries:
            await db.execute(
                """INSERT INTO graph_communities
                   (user_id, title, summary, member_names, member_count,
                    mention_total, entity_count_at_build)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id, s["title"], s["summary"],
                    json.dumps(s["members"], ensure_ascii=False),
                    s["member_count"], s["mention_total"], entity_count,
                ),
            )
        await db.commit()


# =========================================================================
# Read path (retriever channel + page)
# =========================================================================

async def has_communities(user_id: int) -> bool:
    """Cheap SQLite existence check — retriever calls this before anything
    else so an un-built graph costs one indexed COUNT, nothing more."""
    async with get_db() as db:
        async with db.execute(
            "SELECT 1 FROM graph_communities WHERE user_id = ? LIMIT 1",
            (user_id,),
        ) as cur:
            return await cur.fetchone() is not None


async def fetch_community_chunks(
    user_id: int,
    query_embedding: Sequence[float],
    limit: int,
    document_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """Global-search channel: communities similar to the query → the chunks
    mentioning their member entities (FEAT-028).

    Returns normal chunk dicts ({chunk_id, content, metadata, ...}) so the
    result feeds the shared RRF fusion / rerank / citation pipeline; when a
    document scope is active (FEAT-026), out-of-scope chunks are dropped by
    metadata so the channel cannot widen the request's scope.
    """
    settings = get_settings()
    chroma = get_chroma_client()
    hits = await asyncio.to_thread(
        chroma.query_communities, query_embedding, user_id, settings.COMMUNITY_TOP_K,
    )
    member_names: List[str] = []
    for hit in hits:
        for name in hit.get("members") or []:
            if name and name not in member_names:
                member_names.append(name)
    member_names = member_names[:30]
    if not member_names:
        return []

    neo4j = await get_neo4j_client()
    chunk_ids = await neo4j.get_chunks_for_entities(
        entity_names=member_names, user_id=user_id, limit=limit,
    )
    if not chunk_ids:
        return []
    chunks = await asyncio.to_thread(chroma.get_chunks_by_ids, chunk_ids, user_id)
    if document_ids is not None:
        allowed = set(document_ids)
        chunks = [
            c for c in chunks
            if (c.get("metadata") or {}).get("document_id") in allowed
        ]
    return chunks
