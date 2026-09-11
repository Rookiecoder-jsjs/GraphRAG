"""Tests for graph communities + the global-search channel (FEAT-028).

Layers:
  * ``group_entities`` — the pure deterministic partition (networkx greedy
    modularity over RELATES_TO edges, size-filtered, mention-ranked, capped).
  * ``build_communities`` — full pipeline against fakes (neo4j edges/entities,
    LLM, embedding, chroma community collection) + throwaway SQLite: rows
    land with fallback titles when the LLM fails, chroma is cleared BEFORE
    the upsert (a shrinking rebuild must not leave stale community vectors).
  * ``fetch_community_chunks`` — the read path: top communities by summary
    similarity → member entities → their (citable) chunks, document-scope
    filtered.
  * API handlers — GET/POST /api/graph/communities* (direct invocation).
  * Retriever channel — use_graph_rag + existing communities → a "community"
    labelled RRF list appears in the debug payload.
"""
import asyncio
import json
from unittest import mock

import pytest

from app.database import get_db, init_db


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    """Throwaway SQLite per test (suite-wide pattern)."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "community_test.db"))
    from app.config import get_settings

    get_settings.cache_clear()
    asyncio.run(init_db())
    yield tmp_path
    get_settings.cache_clear()


def _edges(*triples):
    return [{"source": s, "target": t, "relation_type": r} for s, t, r in triples]


# =========================================================================
# Pure grouping
# =========================================================================

def test_group_entities_deterministic_and_ranked():
    from app.services.graph_community import group_entities

    edges = _edges(
        ("太阳", "地球", "ORBITS"), ("地球", "月亮", "ORBITS"),
        ("月亮", "潮汐", "CAUSES"),
        ("Python", "FastAPI", "USED_BY"), ("FastAPI", "Pydantic", "DEPENDS_ON"),
    )
    mentions = {"太阳": 9, "地球": 8, "月亮": 7, "潮汐": 6,
                "Python": 5, "FastAPI": 4, "Pydantic": 3}

    first = group_entities(edges, mentions, min_size=3, max_communities=10)
    second = group_entities(edges, mentions, min_size=3, max_communities=10)
    assert first == second  # deterministic
    assert len(first) == 2
    names = [{m["name"] for m in g["members"]} for g in first]
    assert {"太阳", "地球", "月亮"} in names or {"Python", "FastAPI", "Pydantic"} in names
    # Ranked by total mentions desc.
    totals = [g["mention_total"] for g in first]
    assert totals == sorted(totals, reverse=True)


def test_group_entities_size_floor_and_cap():
    from app.services.graph_community import group_entities

    edges = _edges(
        ("a1", "a2", "R"), ("a2", "a3", "R"),            # size-3 community
        ("b1", "b2", "R"),                                # size-2, filtered
    )
    mentions = {n: 1 for n in ("a1", "a2", "a3", "b1", "b2")}
    groups = group_entities(edges, mentions, min_size=3, max_communities=10)
    assert len(groups) == 1
    assert {m["name"] for m in groups[0]["members"]} == {"a1", "a2", "a3"}

    # Cap: keep the highest-mention communities only.
    edges2 = _edges(("x1", "x2", "R"), ("x2", "x3", "R"), ("y1", "y2", "R"), ("y2", "y3", "R"))
    mentions2 = {"x1": 1, "x2": 1, "x3": 1, "y1": 9, "y2": 9, "y3": 9}
    capped = group_entities(edges2, mentions2, min_size=3, max_communities=1)
    assert len(capped) == 1
    assert {m["name"] for m in capped[0]["members"]} == {"y1", "y2", "y3"}


# =========================================================================
# Fakes
# =========================================================================

class _FakeCommunityChroma:
    """Records call order — delete must precede add on every rebuild."""

    def __init__(self):
        self.calls = []
        self._store = []  # in-memory community vectors

    def add_communities(self, ids, documents, metadatas, embeddings):
        self.calls.append("add")
        self._store = list(zip(ids, metadatas))

    def query_communities(self, embedding, user_id, n_results=2):
        self.calls.append("query")
        return [{"id": cid, "score": 0.9, "members": json.loads(m["member_names"])}
                for cid, m in self._store[:n_results]]

    def delete_user_communities(self, user_id):
        self.calls.append("delete")
        self._store = []


class _FakeCommunityNeo4j:
    def __init__(self, edges, entities):
        self._edges = edges
        self._entities = entities

    async def get_user_relation_edges(self, user_id, limit=5000):
        return list(self._edges)

    async def get_user_entities_with_mentions(self, user_id, limit=200):
        return list(self._entities)

    async def count_user_entities(self, user_id):
        return len(self._entities)

    async def get_chunks_for_entities(self, entity_names, user_id, limit):
        return ["ch1", "ch2"]

    async def get_entities_from_chunks(self, chunk_ids, user_id):
        return []

    async def get_related_entities(self, entity_names, user_id, depth=2):
        return {"center_nodes": [], "related_nodes": [], "relations": []}


class _FakeLLM:
    def __init__(self, fail=False):
        self._fail = fail
        self.calls = 0

    async def chat_complete(self, messages, **kwargs):
        self.calls += 1
        if self._fail:
            raise RuntimeError("llm down")
        return '{"title": "太阳系结构", "summary": "太阳与地球月亮的绕转关系。"}'


class _FakeEmbedding:
    async def embed_batch(self, texts):
        return [[0.1, 0.2] for _ in texts]


def _install(monkeypatch, *, edges, entities, llm=None, chroma=None):
    from app.config import get_settings
    import app.services.graph_community as gc

    chroma = chroma or _FakeCommunityChroma()
    llm = llm or _FakeLLM()
    neo4j = _FakeCommunityNeo4j(edges, entities)

    async def _n4j():
        return neo4j

    async def _llm():
        return llm

    async def _emb():
        return _FakeEmbedding()

    monkeypatch.setattr(gc, "get_neo4j_client", _n4j)
    monkeypatch.setattr(gc, "get_llm_service", _llm)
    monkeypatch.setattr(gc, "get_embedding_service", _emb)
    monkeypatch.setattr(gc, "get_chroma_client", lambda: chroma)
    return gc, chroma, llm, neo4j


_ENTITIES = [{"name": n, "mention_count": i + 1}
             for i, n in enumerate(["太阳", "地球", "月亮", "Python", "FastAPI", "Pydantic"])]
_EDGES = _edges(
    ("太阳", "地球", "ORBITS"), ("地球", "月亮", "ORBITS"), ("月亮", "潮汐", "CAUSES"),
    ("Python", "FastAPI", "USED_BY"), ("FastAPI", "Pydantic", "DEPENDS_ON"),
)


# =========================================================================
# build_communities
# =========================================================================

def test_build_persists_rows_and_summaries():
    gc, chroma, llm, _neo = _install(monkeypatch, edges=_EDGES, entities=_ENTITIES)

    stats = asyncio.run(gc.build_communities(1))
    assert stats["communities"] >= 1
    assert llm.calls >= 1

    async def _rows():
        async with get_db() as db:
            async with db.execute(
                "SELECT title, summary, member_names, member_count, mention_total, "
                "entity_count_at_build FROM graph_communities WHERE user_id = 1"
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    rows = asyncio.run(_rows())
    assert len(rows) == stats["communities"]
    titles = {r["title"] for r in rows}
    assert "太阳系结构" in titles
    for r in rows:
        members = json.loads(r["member_names"])
        assert len(members) == r["member_count"]
        assert r["entity_count_at_build"] == len(_ENTITIES)
    # Chroma was cleared before the upsert (shrink-safety) and holds vectors.
    assert chroma.calls == ["delete", "add"]
    assert len(chroma._store) == stats["communities"]


def test_build_llm_failure_falls_back_to_generic_title():
    gc, chroma, llm, _neo = _install(
        monkeypatch, edges=_EDGES, entities=_ENTITIES, llm=_FakeLLM(fail=True),
    )

    stats = asyncio.run(gc.build_communities(1))
    assert stats["communities"] >= 1

    async def _rows():
        async with get_db() as db:
            async with db.execute(
                "SELECT title, summary, member_names FROM graph_communities WHERE user_id = 1"
            ) as cur:
                return [dict(r) for r in await cur.fetchall()]

    rows = asyncio.run(_rows())
    assert rows
    for i, r in enumerate(rows):
        assert r["title"] == f"主题 {i + 1}"
        assert json.loads(r["member_names"])  # summary falls back to members


def test_build_replaces_previous_rows():
    gc, chroma, _llm, _neo = _install(monkeypatch, edges=_EDGES, entities=_ENTITIES)
    asyncio.run(gc.build_communities(1))
    # A rebuild with a shrunk graph must leave no stale rows/vectors.
    asyncio.run(gc.build_communities(2))

    async def _counts():
        async with get_db() as db:
            async with db.execute("SELECT COUNT(*) AS n FROM graph_communities WHERE user_id = 1") as cur:
                u1 = (await cur.fetchone())["n"]
            async with db.execute("SELECT COUNT(*) AS n FROM graph_communities WHERE user_id = 2") as cur:
                u2 = (await cur.fetchone())["n"]
        return u1, u2

    u1, u2 = asyncio.run(_counts())
    assert u1 >= 1 and u2 >= 1  # user isolation on the replace
    assert chroma.calls.count("delete") == 2  # every rebuild clears first


# =========================================================================
# Read path
# =========================================================================

def test_has_communities_and_fetch_chunks():
    gc, chroma, _llm, _neo = _install(monkeypatch, edges=_EDGES, entities=_ENTITIES)

    assert asyncio.run(gc.has_communities(1)) is False
    asyncio.run(gc.build_communities(1))
    assert asyncio.run(gc.has_communities(1)) is True

    class _C:
        def get_chunks_by_ids(self, ids, user_id):
            return [
                {"chunk_id": "ch1", "content": "x", "metadata": {"document_id": "d1"}},
                {"chunk_id": "ch2", "content": "y", "metadata": {"document_id": "d9"}},
            ]

    import app.services.graph_community as gc2
    with mock.patch.object(gc2, "get_chroma_client", lambda: _C()):
        chunks = asyncio.run(gc2.fetch_community_chunks(1, [0.1, 0.2], limit=20))
    assert {c["chunk_id"] for c in chunks} == {"ch1", "ch2"}

    scoped = asyncio.run(gc2.fetch_community_chunks(1, [0.1, 0.2], limit=20,
                                                    document_ids=["d1"]))
    assert {c["chunk_id"] for c in scoped} == {"ch1"}


# =========================================================================
# API handlers
# =========================================================================

def test_communities_endpoints():
    from app.api import communities as api

    gc, _chroma, _llm, neo4j = _install(monkeypatch, edges=_EDGES, entities=_ENTITIES)
    with mock.patch.object(api, "build_communities", mock.AsyncMock(return_value={"communities": 1})):
        stats = asyncio.run(api.rebuild_communities(current_user={"id": 1}))
    assert stats == {"communities": 1}

    with mock.patch.object(api, "get_neo4j_client", _ok(neo4j)):
        resp = asyncio.run(api.list_communities(current_user={"id": 1}))
    assert resp["stale"] is False  # built this instant against the same count
    assert resp["communities"]
    body = resp["communities"][0]
    assert {"id", "title", "summary", "members", "member_count", "mention_total",
            "created_at"} <= set(body.keys())


def _ok(v):
    async def _g():
        return v
    return _g()


# =========================================================================
# Retriever channel
# =========================================================================

def test_retriever_community_channel_in_debug(monkeypatch):
    import app.services.graph_community as gc
    import app.services.retriever as r

    _install(monkeypatch, edges=_EDGES, entities=_ENTITIES)

    async def fake_has(user_id):
        return True

    async def fake_fetch(user_id, embedding, limit, document_ids=None):
        return [{"chunk_id": "ch9", "content": "社区命中",
                 "metadata": {"document_id": "d1"}, "distance": 0.2}]

    monkeypatch.setattr(gc, "has_communities", fake_has)
    monkeypatch.setattr(gc, "fetch_community_chunks", fake_fetch)

    # Minimal pipeline stubs (same shape as test_search_debug.py).
    class _QP:
        async def rewrite_query(self, q, h): return q
        async def generate_query_variants(self, q, n): return []
        async def extract_entities(self, q): return [{"name": "太阳", "type": "CONCEPT"}]

    class _Emb:
        async def embed_batch(self, texts): return [[0.1, 0.2] for _ in texts]

    class _Neo:
        async def get_chunks_for_entities(self, entity_names, user_id, limit): return []
        async def get_entities_from_chunks(self, ids, user_id): return []
        async def get_related_entities(self, names, user_id, depth=2):
            return {"center_nodes": [], "related_nodes": [], "relations": []}

    class _RR:
        async def rerank(self, q, chunks, top_k=5):
            return [dict(c, relevance_score=0.8) for c in chunks[:top_k]]

    class _ChromaMin:
        def search(self, emb, uid, k, document_ids=None): return []
        def get_chunks_by_ids(self, ids, uid): return []
        def get_chunk_context(self, cid, uid, w=1): return []

    class _BM25Min:
        def has_index(self, uid): return True
        def search(self, q, uid, k, document_ids=None): return []

    async def _qp(): return _QP()
    async def _emb(): return _Emb()
    async def _neo4j(): return _Neo()
    async def _rr(): return _RR()

    monkeypatch.setattr(r, "get_query_processor", _qp)
    monkeypatch.setattr(r, "get_embedding_service", _emb)
    monkeypatch.setattr(r, "get_neo4j_client", _neo4j)
    monkeypatch.setattr(r, "get_rerank_service", _rr)
    monkeypatch.setattr(r, "get_chroma_client", lambda: _ChromaMin())
    monkeypatch.setattr(r, "get_bm25_service", lambda: _BM25Min())

    result = asyncio.run(r.retrieve(
        "太阳系的结构是怎样的完整验证查询", 1, top_k=5, use_graph_rag=True, debug=True,
    ))

    labels = [c["label"] for c in result["debug"]["channels"]]
    assert "community" in labels
    kinds = {c["label"]: c["kind"] for c in result["debug"]["channels"]}
    assert kinds["community"] == "community"  # not mis-filed as "vector"
