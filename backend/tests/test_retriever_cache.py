"""Unit tests for the retrieval result cache.

Covers the write-invalidation fix: uploads/deletes must drop cached
retrieval results for that user so deleted documents can't keep being
served for up to a full TTL.
"""
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
