"""Tests for the retrieval debug channel (FEAT-024).

Two layers:
  * Pipeline tests stub every retriever dependency (query processor,
    embedding, chroma, bm25, neo4j, reranker) at the module boundary and
    exercise `retrieve(debug=True)` end to end — asserting the debug
    payload shape, truncation, and above all that debug runs never
    read from nor write to the shared retrieval cache.
  * Endpoint tests invoke `POST /api/search/debug`'s handler directly
    (suite-wide pattern) with `app.services.retriever.retrieve` patched —
    the handler imports it inside the function body, so the module
    attribute is the only patch point.

The debug dict is intentionally untyped at the API boundary; its shape is
owned by app/services/retrieval/debug.py and asserted here.
"""
import asyncio
import hashlib

import pytest
from fastapi import HTTPException

from app.database import get_db, init_db


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    """Throwaway SQLite per test (suite-wide pattern). init_db() runs so the
    pipeline's section-sibling expansion queries hit real (empty) tables
    instead of raising "no such table" noise into `degraded`."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "search_debug_test.db"))
    from app.config import get_settings

    get_settings.cache_clear()
    asyncio.run(init_db())
    yield tmp_path
    get_settings.cache_clear()


# Long enough to clear QUERY_REWRITE_MIN_LEN (=20) so the rewrite path runs.
LONG_Q = "知识图谱检索调试台的完整管线验证查询用例设计"


# =========================================================================
# Pipeline stubs
# =========================================================================

class _FakeQP:
    async def rewrite_query(self, query, history):
        return "改写后的查询"

    async def generate_query_variants(self, query, n):
        return ["变体一", "变体二"]

    async def extract_entities(self, query):
        return [{"name": "实体A", "type": "CONCEPT"}]


class _FakeEmbedding:
    def __init__(self, fail=False):
        self._fail = fail

    async def embed_batch(self, texts):
        if self._fail:
            from app.services.embedding import EmbeddingServiceError

            raise EmbeddingServiceError("provider down")
        return [[0.1, 0.2] for _ in texts]


class _FakeChroma:
    def __init__(self, vector_hits=None, graph_hits=None, neighbours=None):
        self._vector = vector_hits or []
        self._graph = graph_hits or []
        self._neighbours = neighbours or []

    def search(self, embedding, user_id, top_k):
        return list(self._vector)

    def get_chunks_by_ids(self, ids, user_id):
        return [c for c in self._graph if c["chunk_id"] in ids]

    def get_chunk_context(self, chunk_id, user_id, window_size=1):
        return list(self._neighbours)


class _FakeBM25:
    def __init__(self, hits=None):
        self._hits = hits or []

    def has_index(self, user_id):
        return True

    def search(self, query, user_id, top_k):
        return list(self._hits)


class _FakeNeo4j:
    def __init__(self, chunk_ids=None, entities=None, relations=None):
        self._ids = chunk_ids or []
        self._entities = entities or []
        self._relations = relations or []

    async def get_chunks_for_entities(self, entity_names, user_id, limit):
        return list(self._ids)

    async def get_entities_from_chunks(self, chunk_ids, user_id):
        return list(self._entities)

    async def get_related_entities(self, entity_names, user_id, depth=2):
        return {"center_nodes": [], "related_nodes": [],
                "relations": list(self._relations)}


class _FakeRerank:
    async def rerank(self, query, chunks, top_k=5):
        out = []
        for chunk in chunks[:top_k]:
            snapped = dict(chunk)
            snapped["relevance_score"] = 0.9
            out.append(snapped)
        return out


def _install(monkeypatch, *, vector_hits=None, bm25_hits=None, graph_hits=None,
             neighbours=None, embed_fail=False):
    """Patch every retriever dependency at the module boundary."""
    import app.services.retriever as r

    async def _qp():
        return _FakeQP()

    async def _emb():
        return _FakeEmbedding(fail=embed_fail)

    async def _n4j():
        graph_ids = [c["chunk_id"] for c in (graph_hits or [])]
        return _FakeNeo4j(
            chunk_ids=graph_ids,
            entities=[{"name": "实体A", "type": "CONCEPT"}],
        )

    async def _rr():
        return _FakeRerank()

    monkeypatch.setattr(r, "get_query_processor", _qp)
    monkeypatch.setattr(r, "get_embedding_service", _emb)
    monkeypatch.setattr(r, "get_chroma_client",
                        lambda: _FakeChroma(vector_hits, graph_hits, neighbours))
    monkeypatch.setattr(r, "get_bm25_service", lambda: _FakeBM25(bm25_hits))
    monkeypatch.setattr(r, "get_neo4j_client", _n4j)
    monkeypatch.setattr(r, "get_rerank_service", _rr)


# =========================================================================
# Pipeline-level tests
# =========================================================================

def test_retrieve_debug_returns_all_stages(monkeypatch):
    """debug=True must carry every stage snapshot; channel hits are
    truncated snapshots (never references to pipeline chunk dicts)."""
    _install(
        monkeypatch,
        vector_hits=[{"chunk_id": "c1", "content": "向量命中内容",
                      "metadata": {"document_id": "doc-1"}, "distance": 0.1}],
        bm25_hits=[{"id": "c2", "content": "关键词命中内容", "score": 2.5, "rank": 1}],
        graph_hits=[{"chunk_id": "c3", "content": "图谱命中内容",
                     "metadata": {"document_id": "doc-1"}}],
        neighbours=[{"chunk_id": "c1n", "content": "邻居内容", "metadata": {}}],
    )
    import app.services.retriever as r

    result = asyncio.run(r.retrieve(
        LONG_Q, 1, top_k=5, use_graph_rag=True, debug=True,
    ))

    dbg = result["debug"]
    assert dbg["raw_query"] == LONG_Q
    assert dbg["rewritten"] == "改写后的查询"
    assert dbg["rewrite_applied"] is True
    assert dbg["variants"] == ["变体一", "变体二"]
    assert "改写后的查询" in dbg["final_queries"]
    assert dbg["query_entities"] == [{"name": "实体A", "type": "CONCEPT"}]

    channels = {c["label"]: c for c in dbg["channels"]}
    assert {"vector", "bm25", "graph"} <= set(channels)
    assert channels["vector"]["kind"] == "vector"
    v_hits = channels["vector"]["hits"]
    assert v_hits[0]["chunk_id"] == "c1"
    assert len(v_hits[0]["preview"]) <= 160
    assert v_hits[0]["document_id"] == "doc-1"
    # bm25 hits use the "id" key — snapshots normalise to chunk_id.
    assert channels["bm25"]["hits"][0]["chunk_id"] == "c2"
    assert channels["bm25"]["hits"][0]["score"] == 2.5

    assert dbg["fused"] and dbg["fused"][0]["rank"] == 1
    assert all("sources" in f and "rrf_score" in f for f in dbg["fused"])
    assert dbg["seeds"] and all("relevance_score" in s for s in dbg["seeds"])

    prov = {e["chunk_id"]: e["provenance"] for e in dbg["expanded"]}
    assert prov["c1"] == "seed"
    assert prov["c1n"] == "neighbour"

    assert "timing_s" in dbg["diagnostics"]
    assert dbg["diagnostics"]["degraded"] == []
    assert dbg["config"]["use_graph_rag"] is True
    assert dbg["config"]["recall_k"] > 0

    # The debug data rides ON TOP of the regular result, which stays intact.
    assert result["chunks"] and all(c.get("chunk_id") for c in result["chunks"])


def test_retrieve_no_debug_omits_key(monkeypatch):
    """Default (debug=False) responses must not carry the debug key — the
    cached result object is shared across cache hits, and /api/search's
    response_model would drop it anyway."""
    _install(monkeypatch, vector_hits=[
        {"chunk_id": "c1", "content": "x", "metadata": {}, "distance": 0.1},
    ])
    import app.services.retriever as r

    result = asyncio.run(r.retrieve(LONG_Q, 1, top_k=5))
    assert "debug" not in result


def test_debug_bypasses_cache_read(monkeypatch):
    """debug=True must run the real pipeline even when a cache entry exists;
    debug=False must still hit the cache (unchanged behaviour)."""
    import app.services.retriever as r

    monkeypatch.setenv("GRAPH_RAG_MODE", "off")
    from app.config import get_settings

    get_settings.cache_clear()

    calls = {"n": 0}

    async def fake_uncached(**kwargs):
        calls["n"] += 1
        return {"chunks": [{"chunk_id": "fresh"}], "entities": [], "relations": []}

    monkeypatch.setattr(r, "_retrieve_uncached", fake_uncached)
    key = (1, hashlib.sha1(b"q|").hexdigest(), 5, False)
    r._cache.set(key, {"chunks": ["cached"], "entities": [], "relations": []})

    res = asyncio.run(r.retrieve("q", 1, top_k=5, debug=True))
    assert calls["n"] == 1
    assert res["chunks"] == [{"chunk_id": "fresh"}]

    res2 = asyncio.run(r.retrieve("q", 1, top_k=5))
    assert calls["n"] == 1  # served from cache, pipeline not re-run
    assert res2["chunks"] == ["cached"]


def test_debug_never_writes_cache(monkeypatch):
    """All three exit paths (full pipeline, empty fusion, embed failure)
    must leave the shared cache untouched when debug=True."""
    import app.services.retriever as r

    # 1. Full pipeline.
    _install(monkeypatch, vector_hits=[
        {"chunk_id": "c1", "content": "x", "metadata": {}, "distance": 0.1},
    ], bm25_hits=[{"id": "c2", "content": "y", "score": 1.0, "rank": 1}])
    r._cache._store.clear()
    res = asyncio.run(r.retrieve(LONG_Q, 1, top_k=5, debug=True))
    assert "debug" in res
    assert r._cache._store == {}

    # 2. Empty fusion (every channel empty).
    _install(monkeypatch)
    r._cache._store.clear()
    res = asyncio.run(r.retrieve(LONG_Q, 1, top_k=5, debug=True))
    assert res["chunks"] == []
    assert "fused" in res["debug"]
    assert r._cache._store == {}

    # 3. Embedding failure — degraded must reach the debug payload.
    _install(monkeypatch, embed_fail=True)
    r._cache._store.clear()
    res = asyncio.run(r.retrieve(LONG_Q, 1, top_k=5, debug=True))
    assert res["chunks"] == []
    assert "embed_failed" in res["debug"]["diagnostics"]["degraded"]
    assert r._cache._store == {}


def test_debug_channel_hits_truncated(monkeypatch):
    """Per-channel hits are capped and previews clipped to the snapshot
    limit — a recall_k-sized channel must not blow up the payload."""
    _install(monkeypatch, vector_hits=[
        {"chunk_id": f"c{i}", "content": "x" * 400, "metadata": {}, "distance": 0.1}
        for i in range(30)
    ])
    import app.services.retriever as r
    from app.services.retrieval.debug import _PREVIEW_LIMIT, _MAX_CHANNEL_HITS

    result = asyncio.run(r.retrieve(LONG_Q, 1, top_k=5, debug=True))
    channels = {c["label"]: c for c in result["debug"]["channels"]}
    hits = channels["vector"]["hits"]
    assert len(hits) <= _MAX_CHANNEL_HITS
    assert all(len(h["preview"]) <= _PREVIEW_LIMIT for h in hits)


def test_debug_short_query_skips_rewrite(monkeypatch):
    """A query shorter than QUERY_REWRITE_MIN_LEN skips the rewrite call —
    the snapshot must say so instead of pretending the raw query was used."""
    _install(monkeypatch, vector_hits=[
        {"chunk_id": "c1", "content": "x", "metadata": {}, "distance": 0.1},
    ])
    import app.services.retriever as r

    result = asyncio.run(r.retrieve("短查询", 1, top_k=5, debug=True))
    dbg = result["debug"]
    assert dbg["rewrite_applied"] is False
    assert dbg["rewritten"] == "短查询"
    assert dbg["final_queries"] == ["短查询"]


# =========================================================================
# Endpoint tests — POST /api/search/debug
# =========================================================================

async def _bootstrap_docs():
    await init_db()
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'u1', 'x')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO documents (id, user_id, title, original_filename, status) "
            "VALUES ('doc-1', 1, '文档一', 'a.md', 'completed')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO documents (id, user_id, title, original_filename, status) "
            "VALUES ('doc-2', 1, '文档二', 'b.md', 'completed')"
        )
        await db.commit()


def _fake_debug_payload():
    """Shape produced by DebugCollector.finish() for the title-join test."""
    return {
        "raw_query": "q",
        "channels": [{
            "label": "vector", "kind": "vector",
            "hits": [{"chunk_id": "c1", "preview": "x", "document_id": "doc-1"}],
        }],
        "fused": [{"chunk_id": "c1", "rrf_score": 0.1, "rank": 1,
                   "sources": ["vector"], "document_id": "doc-1"}],
        "seeds": [{"chunk_id": "c1", "rank": 1, "relevance_score": 0.9,
                   "document_id": "doc-1"}],
        "expanded": [{"chunk_id": "c2", "provenance": "neighbour",
                      "document_id": "doc-2"}],
        "diagnostics": {"degraded": [], "timing_s": {"total": 0.1}},
        "config": {"top_k": 5, "use_graph_rag": False},
    }


def test_search_debug_endpoint_returns_debug_and_titles():
    from unittest import mock

    from app.models.chat import SearchRequest
    from app.api import search as search_mod

    asyncio.run(_bootstrap_docs())
    payload = _fake_debug_payload()

    async def fake_retrieve(*args, **kwargs):
        assert kwargs.get("debug") is True
        assert kwargs.get("use_graph_rag") is False
        return {"chunks": [], "entities": [], "relations": [], "debug": payload}

    with mock.patch("app.services.retriever.retrieve", fake_retrieve):
        resp = asyncio.run(search_mod.search_debug(
            request=SearchRequest(query="q", top_k=5),
            current_user={"id": 1},
        ))

    assert resp.debug == payload
    assert resp.titles == {"doc-1": "文档一", "doc-2": "文档二"}
    assert resp.query == "q"


def test_search_debug_titles_partial_for_unknown_docs():
    """document_ids without a matching documents row are simply absent from
    the map — the debug page falls back to the chunk_id."""
    from unittest import mock

    from app.models.chat import SearchRequest
    from app.api import search as search_mod

    asyncio.run(_bootstrap_docs())
    payload = _fake_debug_payload()
    payload["expanded"][0]["document_id"] = "doc-gone"

    async def fake_retrieve(*args, **kwargs):
        return {"chunks": [], "entities": [], "relations": [], "debug": payload}

    with mock.patch("app.services.retriever.retrieve", fake_retrieve):
        resp = asyncio.run(search_mod.search_debug(
            request=SearchRequest(query="q", top_k=5),
            current_user={"id": 1},
        ))

    assert resp.titles == {"doc-1": "文档一"}


def test_search_debug_gate_timeout_429():
    from unittest import mock

    from app.models.chat import SearchRequest
    from app.api import search as search_mod
    from app.services.query_gate import QueryGateTimeout

    async def boom(*args, **kwargs):
        raise QueryGateTimeout(retry_after=3.0)

    with mock.patch("app.services.retriever.retrieve", boom):
        with pytest.raises(HTTPException) as ei:
            asyncio.run(search_mod.search_debug(
                request=SearchRequest(query="q", top_k=5),
                current_user={"id": 1},
            ))

    assert ei.value.status_code == 429
    assert ei.value.headers["Retry-After"] == "3"


def test_search_request_accepts_use_graph_rag():
    """The additive field must be constructible and default to False so old
    clients (which never send it) keep today's behaviour."""
    from app.models.chat import SearchRequest

    req = SearchRequest(query="q", top_k=5, use_graph_rag=True)
    assert req.use_graph_rag is True
    assert SearchRequest(query="q").use_graph_rag is False
