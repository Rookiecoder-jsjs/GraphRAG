"""Tests for the composable retrieval pipeline (Commit 2, #5).

Standalone runner (no pytest needed):
    cd backend
    ../.venv/Scripts/python.exe tests/test_retrieval_pipeline.py

Covers:
  1. parse_pipeline / build_pipeline — CSV parsing, unknown-step failure
  2. Step-level behavior with fakes: graph_retrieve (gating + fallback),
     rrf_fuse (graph prepend + dedup), image_promote (hot/cold math),
     context_enrich (dedup vs append), entity_enrich (append vs none)
  3. build_rag_context parity — full chat pipeline on scripted fakes,
     including use_graph_rag step injection
"""
from __future__ import annotations

import asyncio
import sys
import unittest.mock as _mock
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import app.services.retrieval.steps as steps_mod  # noqa: E402
import app.api.chat as chat_mod  # noqa: E402
from app.services.retrieval import (  # noqa: E402
    RetrievalContext,
    build_pipeline,
    parse_pipeline,
    run_pipeline,
)


PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_failures: list = []


def check(name: str, cond: bool, detail: str = ""):
    status = PASS if cond else FAIL
    suffix = f" — {detail}" if detail and not cond else ""
    print(f"  [{status}] {name}{suffix}")
    if not cond:
        _failures.append(name)


class _S:
    """Minimal settings stand-in (only the attrs steps read)."""

    QUERY_REWRITE_MIN_LEN = 20
    RERANK_RECALL_K = 25
    IMAGE_PROMOTION_THRESHOLD = 0.45
    IMAGE_RESULT_QUOTA = 2


def _ctx(**overrides) -> RetrievalContext:
    base = dict(
        query="hello world",
        user_id=1,
        settings=_S(),
        top_k=2,
        vector_recall=4,
        bm25_recall=4,
    )
    base.update(overrides)
    return RetrievalContext(**base)


# =========================================================================
# 1. parse_pipeline / build_pipeline
# =========================================================================

def test_parse_pipeline():
    check("parse_pipeline: splits CSV, drops empties",
          parse_pipeline("a, b ,,c") == ["a", "b", "c"])


def test_build_pipeline():
    steps = build_pipeline(["query_embed", "rerank", "entity_enrich"])
    check("build_pipeline: known names → ordered step instances",
          [s.name for s in steps] == ["query_embed", "rerank", "entity_enrich"])
    try:
        build_pipeline(["bogus_step"])
        check("build_pipeline: unknown name raises", False)
    except ValueError as e:
        check("build_pipeline: unknown name raises listing known steps",
              "bogus_step" in str(e) and "query_embed" in str(e))


# =========================================================================
# 2. Step-level behavior
# =========================================================================

class _FakeQueryProcessor:
    def __init__(self, rewritten=None, entities=None, raise_on_extract=False):
        self.rewritten = rewritten
        self.entities = entities or []
        self.raise_on_extract = raise_on_extract

    async def rewrite_query(self, query):
        return self.rewritten if self.rewritten is not None else query

    async def extract_entities(self, query):
        if self.raise_on_extract:
            raise RuntimeError("boom")
        return self.entities


class _FakeNeo4j:
    def __init__(self, chunk_ids=None, entities=None, relations=None):
        self.chunk_ids = chunk_ids or []
        self.entities = entities or []
        self.relations = relations or []

    async def get_chunks_for_entities(self, entity_names, user_id, limit=100):
        return self.chunk_ids

    async def get_entities_from_chunks(self, chunk_ids, user_id):
        return self.entities

    async def get_related_entities(self, entity_names, user_id, depth=2):
        return {"relations": self.relations}


class _FakeChroma:
    def __init__(self, by_id, neighbors=None):
        self.by_id = by_id  # chunk_id -> {chunk_id, content, metadata}
        self.neighbors = neighbors or {}  # chunk_id -> [neighbor dicts]

    def search(self, query_embedding, user_id, top_k=5):
        return list(self.by_id.values())[:top_k]

    def get_chunks_by_ids(self, chunk_ids, user_id):
        return [self.by_id[cid] for cid in chunk_ids if cid in self.by_id]

    def get_chunk_context(self, chunk_id, user_id, window_size=1):
        return list(self.neighbors.get(chunk_id, []))


def test_graph_retrieve_gating_and_fallback():
    qp = _FakeQueryProcessor(entities=[{"name": "Python", "type": "TECHNOLOGY"}])
    neo4j = _FakeNeo4j(chunk_ids=["g1"])
    chroma = _FakeChroma({"g1": {"chunk_id": "g1", "content": "graph hit"}})
    # get_query_processor / get_neo4j_client are async getters — the step
    # awaits their result, so the patch must return an awaitable.
    with _mock.patch.object(steps_mod, "get_query_processor",
                            _mock.AsyncMock(return_value=qp)), \
         _mock.patch.object(steps_mod, "get_neo4j_client",
                            _mock.AsyncMock(return_value=neo4j)), \
         _mock.patch.object(steps_mod, "get_chroma_client",
                            lambda: chroma):
        # use_graph_rag=False → no-op
        ctx = _ctx(use_graph_rag=False)
        asyncio.run(run_pipeline(build_pipeline(["graph_retrieve"]), ctx))
        check("graph_retrieve: skipped when use_graph_rag=False",
              ctx.graph_results == [])

        # use_graph_rag=True → hydrates graph chunks
        ctx = _ctx(use_graph_rag=True)
        asyncio.run(run_pipeline(build_pipeline(["graph_retrieve"]), ctx))
        check("graph_retrieve: hydrates chunks for extracted entities",
              ctx.graph_results == [{"chunk_id": "g1", "content": "graph hit"}])

        # extraction failure → silent fallback, no crash
        qp.raise_on_extract = True
        ctx = _ctx(use_graph_rag=True)
        asyncio.run(run_pipeline(build_pipeline(["graph_retrieve"]), ctx))
        check("graph_retrieve: extraction failure falls back silently",
              ctx.graph_results == [])


def test_rrf_fuse_prepends_graph_hits():
    vector = [
        {"chunk_id": "v1", "content": "v1"},
        {"chunk_id": "v2", "content": "v2"},
    ]
    bm25 = [
        {"chunk_id": "v2", "content": "v2"},
        {"chunk_id": "v3", "content": "v3"},
    ]
    ctx = _ctx(
        use_hybrid=True,
        vector_results=vector,
        bm25_results=bm25,
        graph_results=[{"chunk_id": "g1", "content": "graph"}],
    )
    asyncio.run(run_pipeline(build_pipeline(["rrf_fuse"]), ctx))
    # RRF: v2 ranks in both lanes (1/61 + 1/62) > v1 (1/61) > v3 (1/62).
    check("rrf_fuse: graph hits prepended, hybrid deduped",
          [c["chunk_id"] for c in ctx.fused] == ["g1", "v2", "v1", "v3"],
          f"got {[c['chunk_id'] for c in ctx.fused]}")
    # Prepended graph chunks bypass fusion (no score); the fused tail has one.
    check("rrf_fuse: fused chunks carry rrf_score",
          all(c.get("rrf_score") is not None for c in ctx.fused[1:])
          and ctx.fused[0].get("rrf_score") is None)


def test_image_promote_hot_and_cold():
    hot_img = {"chunk_id": "img-hot", "distance": 0.2,  # sim 0.8
               "metadata": {"modality": "image"}}
    cold_img = {"chunk_id": "img-cold", "distance": 0.7,  # sim 0.3
                "metadata": {"modality": "image"}}
    texts = [{"chunk_id": f"t{i}", "content": "x"} for i in range(3)]
    ctx = _ctx(top_k=3, reranked_chunks=[*texts, hot_img, cold_img])
    asyncio.run(run_pipeline(build_pipeline(["image_promote"]), ctx))
    check("image_promote: hot image promoted ahead of texts",
          ctx.reranked_chunks[0]["chunk_id"] == "img-hot",
          f"order={[c['chunk_id'] for c in ctx.reranked_chunks]}")


def test_context_enrich_dedup_policy():
    neighbor = {"chunk_id": "n1", "content": "neighbor"}
    ctx = _ctx(
        dedup_context=True,
        reranked_chunks=[
            {"chunk_id": "a", "content": "a"},
            {"chunk_id": "b", "content": "b"},
        ],
    )
    chroma = _FakeChroma({}, neighbors={"a": [neighbor], "b": [neighbor]})
    with _mock.patch.object(steps_mod, "get_chroma_client", lambda: chroma):
        asyncio.run(run_pipeline(build_pipeline(["context_enrich"]), ctx))
    check("context_enrich: dedup mode keeps one copy of shared neighbor",
          [c["chunk_id"] for c in ctx.chunks] == ["a", "b", "n1"],
          f"got {[c['chunk_id'] for c in ctx.chunks]}")


def test_entity_enrich_append_related():
    neo4j = _FakeNeo4j(
        entities=[{"name": "Python", "type": "TECHNOLOGY"}],
        relations=[{"source": "Python", "target": "AI", "relation_type": "used_by"}],
    )
    ctx = _ctx(
        chunks=[{"chunk_id": "a", "content": "a"}],
        entity_name_limit=3, entity_depth=2, append_related=True,
    )
    with _mock.patch.object(steps_mod, "get_neo4j_client",
                            _mock.AsyncMock(return_value=neo4j)):
        asyncio.run(run_pipeline(build_pipeline(["entity_enrich"]), ctx))
    names = [e["name"] for e in ctx.entities]
    check("entity_enrich: appends 'Related'-typed neighbours (chat)",
          "Python" in names and "AI" in names
          and any(e.get("type") == "Related" for e in ctx.entities)
          and ctx.relations == neo4j.relations,
          f"names={names}, relations={ctx.relations}")

    # search mode: no append, relations only when >= 2 entities
    neo4j2 = _FakeNeo4j(
        entities=[{"name": "Only", "type": "CONCEPT"}],
        relations=[{"source": "Only", "target": "Other", "relation_type": "rel"}],
    )
    ctx2 = _ctx(chunks=[{"chunk_id": "a", "content": "a"}],
                entity_name_limit=5, entity_depth=1, append_related=False)
    with _mock.patch.object(steps_mod, "get_neo4j_client",
                            _mock.AsyncMock(return_value=neo4j2)):
        asyncio.run(run_pipeline(build_pipeline(["entity_enrich"]), ctx2))
    check("entity_enrich: search mode skips relations with <2 entities",
          ctx2.relations == [] and len(ctx2.entities) == 1)


# =========================================================================
# 3. build_rag_context parity (full chat pipeline on fakes)
# =========================================================================

class _FakeEmbedding:
    async def embed_single(self, text, use_cache=True):
        return [0.1, 0.2, 0.3]


class _FakeBM25:
    def has_index(self, user_id):
        return True

    def search(self, query, user_id, top_k=25):
        return [
            {"chunk_id": "bm25-a", "content": "bm25 a"},
            {"chunk_id": "bm25-b", "content": "bm25 b"},
        ]


class _FakeRerank:
    async def rerank(self, query, chunks, top_k=5):
        out = []
        for i, c in enumerate(chunks[:top_k]):
            c = dict(c)
            c["relevance_score"] = 1.0 / (i + 1)
            out.append(c)
        return out


def test_build_rag_context_pipeline_parity():
    chroma = _FakeChroma({
        "vec-a": {"chunk_id": "vec-a", "content": "vector a",
                  "metadata": {"document_id": "doc1"}},
        "vec-b": {"chunk_id": "vec-b", "content": "vector b",
                  "metadata": {"document_id": "doc1"}},
        "g1": {"chunk_id": "g1", "content": "graph hit",
               "metadata": {"document_id": "doc1"}},
    })
    neo4j = _FakeNeo4j(
        chunk_ids=["g1"],
        entities=[{"name": "Python", "type": "TECHNOLOGY"}],
        relations=[{"source": "Python", "target": "AI", "relation_type": "rel"}],
    )
    qp = _FakeQueryProcessor(rewritten="rewritten hello world",
                             entities=[{"name": "Python", "type": "TECHNOLOGY"}])

    with _mock.patch.object(steps_mod, "get_embedding_service",
                            _mock.AsyncMock(return_value=_FakeEmbedding())), \
         _mock.patch.object(steps_mod, "get_chroma_client", lambda: chroma), \
         _mock.patch.object(steps_mod, "get_bm25_service", lambda: _FakeBM25()), \
         _mock.patch.object(steps_mod, "get_neo4j_client",
                            _mock.AsyncMock(return_value=neo4j)), \
         _mock.patch.object(steps_mod, "get_query_processor",
                            _mock.AsyncMock(return_value=qp)), \
         _mock.patch.object(steps_mod, "get_rerank_service",
                            _mock.AsyncMock(return_value=_FakeRerank())):
        # use_graph_rag=True → the wrapper injects the graph step; the
        # graph hit is prepended by rrf_fuse and survives reranking first.
        ctx_out = asyncio.run(chat_mod.build_rag_context(
            "hello world", user_id=1, use_graph_rag=True,
        ))

    check("build_rag_context: returns the historical shape",
          set(ctx_out.keys()) == {"chunks", "entities", "relations"})
    check("build_rag_context: graph hit is the first chunk (prepend)",
          ctx_out["chunks"][0]["chunk_id"] == "g1",
          f"order={[c['chunk_id'] for c in ctx_out['chunks'][:4]]}")
    check("build_rag_context: entities/relations enriched",
          any(e["name"] == "Python" for e in ctx_out["entities"])
          and ctx_out["relations"] == neo4j.relations)
    check("build_rag_context: reranked chunks carry relevance_score",
          all(c.get("relevance_score") is not None
              for c in ctx_out["chunks"][:3]))


ALL_TESTS = [
    test_parse_pipeline,
    test_build_pipeline,
    test_graph_retrieve_gating_and_fallback,
    test_rrf_fuse_prepends_graph_hits,
    test_image_promote_hot_and_cold,
    test_context_enrich_dedup_policy,
    test_entity_enrich_append_related,
    test_build_rag_context_pipeline_parity,
]


def test_all_retrieval_pipeline_checks_passed():
    """pytest mirror: every case above already ran at import time."""
    assert not _failures, (
        f"{len(_failures)} retrieval pipeline checks failed: "
        f"{', '.join(_failures)}"
    )


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks for retrieval pipeline...")
    for fn in ALL_TESTS:
        try:
            fn()
        except Exception as e:
            check(f"{fn.__name__}: no unhandled exceptions", False, repr(e))
    print()
    if _failures:
        print(f"{FAIL} {len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print(f"{PASS} All checks passed ({len(ALL_TESTS)} tests).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
