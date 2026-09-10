"""Unit tests for the retrieval result cache.

Covers the write-invalidation fix: uploads/deletes must drop cached
retrieval results for that user so deleted documents can't keep being
served for up to a full TTL.
"""
import asyncio
import hashlib
import time

from app.services import retriever
from app.services.retriever import _RetrievalCache, invalidate_retrieval_cache


class TestRetrievalCacheInvalidation:
    def setup_method(self):
        self.cache = _RetrievalCache(max_entries=16)

    def test_invalidate_user_drops_only_that_user(self):
        self.cache.set((1, "k1", 5, False), {"chunks": []})
        self.cache.set((1, "k2", 5, False), {"chunks": []})
        self.cache.set((2, "k3", 5, False), {"chunks": []})
        self.cache.invalidate_user(1)
        assert list(self.cache._store.keys()) == [(2, "k3", 5, False)]

    def test_module_level_invalidation(self):
        # Reset the shared singleton so the test is deterministic.
        retriever._cache._store.clear()
        retriever._cache.set((7, "x", 5, False), {"chunks": []})
        invalidate_retrieval_cache(7)
        assert retriever._cache.get((7, "x", 5, False), ttl=300) is None

    def test_lru_eviction(self):
        cache = _RetrievalCache(max_entries=2)
        cache.set((1, "a", 5, False), 1)
        cache.set((1, "b", 5, False), 2)
        cache.set((1, "c", 5, False), 3)  # evicts "a" (oldest)
        assert cache.get((1, "a", 5, False), ttl=300) is None
        assert cache.get((1, "c", 5, False), ttl=300) == 3

    def test_ttl_expiry(self):
        cache = _RetrievalCache()
        cache._store[(1, "a", 5, False)] = (time.time() - 1000, "stale")
        assert cache.get((1, "a", 5, False), ttl=300) is None
        assert "stale" not in cache._store


class TestRetrieveWrapperSplit:
    """retrieve() must consult the cache first and delegate ONLY the miss
    path to _retrieve_uncached (the admission gate wraps that delegate call,
    so cache hits must never reach it)."""

    def setup_method(self):
        retriever._cache._store.clear()

    def teardown_method(self):
        retriever._cache._store.clear()

    def test_miss_delegates_to_uncached(self, monkeypatch):
        sentinel = {"chunks": [], "entities": [], "relations": []}
        calls = []

        async def fake_uncached(**kwargs):
            calls.append(kwargs)
            return sentinel

        monkeypatch.setattr(retriever, "_retrieve_uncached", fake_uncached)
        result = asyncio.run(retriever.retrieve("q", 1))
        assert result is sentinel
        assert len(calls) == 1
        assert calls[0]["query"] == "q" and calls[0]["user_id"] == 1

    @staticmethod
    def _expected_key(user_id: int, query: str, top_k: int) -> tuple:
        # Mirror retrieve()'s key derivation: sha1("query|history") with no
        # history, and use_graph_rag forced True unless GRAPH_RAG_MODE=off
        # (the default "auto" mode resolves to True).
        digest = hashlib.sha1(f"{query}|".encode()).hexdigest()
        from app.config import get_settings
        graph = get_settings().GRAPH_RAG_MODE.lower() != "off"
        return (user_id, digest, top_k, graph)

    def test_hit_never_reaches_uncached(self, monkeypatch):
        retriever._cache.set(self._expected_key(1, "q", 5), {"chunks": ["hit"]})

        def boom(**kwargs):
            raise AssertionError("cache hit must not run the pipeline")

        monkeypatch.setattr(retriever, "_retrieve_uncached", boom)
        result = asyncio.run(retriever.retrieve("q", 1))
        assert result == {"chunks": ["hit"]}

    def test_hit_key_includes_top_k_and_graph(self, monkeypatch):
        retriever._cache.set(self._expected_key(1, "q", 5), {"chunks": ["a"]})
        retriever._cache.set(self._expected_key(1, "q", 10), {"chunks": ["b"]})

        def boom(**kwargs):
            raise AssertionError("cache hit must not run the pipeline")

        monkeypatch.setattr(retriever, "_retrieve_uncached", boom)
        assert asyncio.run(retriever.retrieve("q", 1, top_k=10))["chunks"] == ["b"]
