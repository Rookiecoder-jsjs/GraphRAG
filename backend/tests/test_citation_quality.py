"""Tests for citation quality (#6).

Standalone runner:
    cd backend
    ../.venv/Scripts/python.exe tests/test_citation_quality.py

Coverage:
  1. RerankService.rerank returns chunks with relevance_score attached
  2. Helper `_quality_for_score` maps scores to high/medium/low bands
  3. `_build_citation_context` propagates relevance_score + quality to each
     source row (when present on the chunk)
  4. Missing / non-finite score → defaults to medium (no quality crash)
  5. Coverage ratio helper: (# of cited chunks) / total_chunks
  6. ChatResponse envelope includes citation_coverage (0.0–1.0)
"""
from __future__ import annotations

import asyncio
import inspect
import math
import sys
import unittest.mock as _mock
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# =========================================================================
# Standalone runner
# =========================================================================

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_failures: list = []


def check(name: str, cond: bool, detail: str = ""):
    status = PASS if cond else FAIL
    suffix = f" — {detail}" if detail and not cond else ""
    print(f"  [{status}] {name}{suffix}")
    if not cond:
        _failures.append(name)


# =========================================================================
# 1. RerankService.rerank attaches relevance_score
# =========================================================================

def test_rerank_returns_chunks_with_relevance_score():
    """The reranker calls a remote API; we mock the HTTP response. The
    service should return chunks with a `relevance_score` field matching
    the API's `relevance_score` (or `score` for some vendors)."""
    from app.services.reranker import RerankService

    svc = RerankService.__new__(RerankService)
    svc.base_url = "https://example.test"
    svc.api_key = "fake"
    svc.model = "fake-rerank"
    svc._client = None

    chunks = [
        {"chunk_id": "a", "content": "first"},
        {"chunk_id": "b", "content": "second"},
        {"chunk_id": "c", "content": "third"},
    ]

    fake_resp = _mock.AsyncMock()
    fake_resp.raise_for_status = _mock.Mock()
    fake_resp.json = _mock.Mock(return_value={
        "results": [
            {"index": 0, "relevance_score": 0.92},
            {"index": 2, "relevance_score": 0.31},
            {"index": 1, "relevance_score": 0.55},
        ]
    })
    fake_client = _mock.AsyncMock()
    fake_client.post = _mock.AsyncMock(return_value=fake_resp)
    svc._get_client = _mock.AsyncMock(return_value=fake_client)

    out = asyncio.run(svc.rerank("query", chunks, top_k=3))

    check("rerank: returns 3 chunks", len(out) == 3)
    check("rerank: first chunk has relevance_score == 0.92",
          abs(out[0]["relevance_score"] - 0.92) < 1e-6)
    check("rerank: each chunk carries a float score",
          all(isinstance(c.get("relevance_score"), float) for c in out))
    check("rerank: order matches API order, not original order",
          [c["chunk_id"] for c in out] == ["a", "c", "b"])


def test_rerank_handles_vendor_score_field():
    """Some rerank APIs use `score` instead of `relevance_score`. We
    should accept both (defensive — siliconflow uses relevance_score
    today but jina / cohere use different field names)."""
    from app.services.reranker import RerankService

    svc = RerankService.__new__(RerankService)
    svc.base_url = "https://example.test"
    svc.api_key = "fake"
    svc.model = "fake-rerank"
    svc._client = None

    fake_resp = _mock.AsyncMock()
    fake_resp.raise_for_status = _mock.Mock()
    fake_resp.json = _mock.Mock(return_value={
        "results": [
            {"index": 0, "score": 0.8},  # vendor B style
        ]
    })
    fake_client = _mock.AsyncMock()
    fake_client.post = _mock.AsyncMock(return_value=fake_resp)
    svc._get_client = _mock.AsyncMock(return_value=fake_client)

    out = asyncio.run(svc.rerank("q", [{"chunk_id": "a", "content": "x"}], top_k=1))
    check("rerank: accepts 'score' as fallback field name",
          abs(out[0]["relevance_score"] - 0.8) < 1e-6)


# =========================================================================
# 2. _quality_for_score — band mapping
# =========================================================================

def test_quality_for_score_bands():
    """The band thresholds are part of the public contract. Documenting
    the exact values via tests so a future tweak is intentional."""
    from app.api import chat as chat_mod
    fn = getattr(chat_mod, "_quality_for_score", None)
    check("_quality_for_score exists", callable(fn))
    if fn is None:
        return
    check("score 0.95 → high",  fn(0.95) == "high")
    check("score 0.70 → high",  fn(0.70) == "high")
    check("score 0.69 → medium", fn(0.69) == "medium")
    check("score 0.40 → medium", fn(0.40) == "medium")
    check("score 0.39 → low",   fn(0.39) == "low")
    check("score 0.0  → low",    fn(0.0) == "low")
    check("score 1.0  → high",  fn(1.0) == "high")
    check("score 0.50 → medium", fn(0.50) == "medium")


def test_quality_for_score_handles_bad_input():
    """None / NaN / out-of-range → safe default ('medium' is a neutral
    middle ground; we never want a crash on the chat hot path)."""
    from app.api import chat as chat_mod
    fn = chat_mod._quality_for_score
    check("None → 'medium'",     fn(None) == "medium")
    check("NaN → 'medium'",      fn(float("nan")) == "medium")
    check("negative → 'medium'", fn(-0.1) == "medium")
    check(">1.0 → 'medium'",     fn(1.5) == "medium")
    check("'0.9' string → 'high' (parseable as float)",
          fn("0.9") == "high")
    check("truly unparseable string → 'medium'",
          fn("not a number") == "medium")


# =========================================================================
# 3. _build_citation_context propagates relevance_score + quality
# =========================================================================

SAMPLE_CHUNKS_WITH_SCORES = [
    {"chunk_id": "c1", "content": "alpha", "relevance_score": 0.85,
     "metadata": {"user_id": "1", "document_id": "doc-a", "hierarchy_path": ""}},
    {"chunk_id": "c2", "content": "beta",  "relevance_score": 0.42,
     "metadata": {"user_id": "1", "document_id": "doc-a", "hierarchy_path": ""}},
    {"chunk_id": "c3", "content": "gamma", "relevance_score": 0.15,
     "metadata": {"user_id": "1", "document_id": "doc-a", "hierarchy_path": ""}},
]


class _FakeRow:
    def __init__(self, id, title, original_filename):
        self._d = {"id": id, "title": title, "original_filename": original_filename}
    def __getitem__(self, k): return self._d[k]


class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def fetchall(self): return list(self._rows)


class _Ctx:
    def __init__(self, rows):
        self._rows = rows
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    def execute(self, sql, params):
        return _FakeCursor(self._rows)


def _fake_get_db():
    def _stub():
        return _Ctx([_FakeRow("doc-a", "Doc A", "a.pdf")])
    return _stub


def test_build_citation_context_adds_score_and_quality_per_source():
    """Each source row in the response should carry relevance_score +
    quality, derived from the chunk the source came from."""
    from app.api import chat as chat_mod
    with _mock.patch.object(chat_mod, "get_db", _fake_get_db()):
        result = asyncio.run(chat_mod._build_citation_context(
            chunks=SAMPLE_CHUNKS_WITH_SCORES, user_id=1,
        ))
    sources = result["sources"]
    check("sources: one per chunk", len(sources) == 3)
    check("source 0: score 0.85 → high",
          sources[0]["relevance_score"] == 0.85
          and sources[0]["quality"] == "high")
    check("source 1: score 0.42 → medium",
          sources[1]["relevance_score"] == 0.42
          and sources[1]["quality"] == "medium")
    check("source 2: score 0.15 → low",
          sources[2]["relevance_score"] == 0.15
          and sources[2]["quality"] == "low")


def test_build_citation_context_handles_missing_score():
    """If a chunk has no relevance_score (e.g. retrieved via graph-RAG
    and not reranked, or reranker fell back to original order), we
    must not crash — default to medium and null score."""
    from app.api import chat as chat_mod
    chunks_no_score = [
        {"chunk_id": "c1", "content": "alpha",
         "metadata": {"user_id": "1", "document_id": "doc-a", "hierarchy_path": ""}},
    ]
    with _mock.patch.object(chat_mod, "get_db", _fake_get_db()):
        result = asyncio.run(chat_mod._build_citation_context(
            chunks=chunks_no_score, user_id=1,
        ))
    src = result["sources"][0]
    check("missing score: relevance_score is None",
          src.get("relevance_score") is None)
    check("missing score: quality is 'medium' (safe default)",
          src.get("quality") == "medium")


# =========================================================================
# 4. _citation_coverage helper
# =========================================================================

def test_citation_coverage_ratio():
    from app.api import chat as chat_mod
    fn = getattr(chat_mod, "_citation_coverage", None)
    check("_citation_coverage exists", callable(fn))
    if fn is None:
        return
    check("coverage 2/3 ≈ 0.667",
          abs(fn(num_cited_markers=2, num_sources=3) - 2/3) < 1e-6)
    check("no sources → 0.0",  fn(0, 0) == 0.0)
    check("all sources cited → 1.0", fn(3, 3) == 1.0)
    check("clamped: markers > sources → 1.0", fn(10, 3) == 1.0)
    check("clamped: negative markers → 0.0", fn(-1, 3) == 0.0)


# =========================================================================
# 5. Chat response envelope shape
# =========================================================================

def test_chat_response_envelope_has_coverage_field():
    """ChatResponse should now carry a `citation_coverage` field
    (0.0-1.0) so the front-end can render a coverage bar without
    re-deriving it from the sources array."""
    from app.models.chat import ChatResponse
    resp = ChatResponse(message="x", conversation_id="c1")
    check("ChatResponse: has citation_coverage field",
          hasattr(resp, "citation_coverage"))
    check("ChatResponse: citation_coverage defaults to 0.0",
          resp.citation_coverage == 0.0)


# =========================================================================
# Driver
# =========================================================================

ALL_TESTS = [
    test_rerank_returns_chunks_with_relevance_score,
    test_rerank_handles_vendor_score_field,
    test_quality_for_score_bands,
    test_quality_for_score_handles_bad_input,
    test_build_citation_context_adds_score_and_quality_per_source,
    test_build_citation_context_handles_missing_score,
    test_citation_coverage_ratio,
    test_chat_response_envelope_has_coverage_field,
]


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks for citation quality...")
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
