"""Tests for #9 cross-document comparison.

Standalone runner:
    cd backend
    ../.venv/Scripts/python.exe tests/test_compare.py

Coverage:
  1. ChatRequest.compare_mode defaults to False
  2. _build_citation_context formats the prompt context with "(from: Doc
     Title)" inline markers when comparison_mode=True; without them
     when False. We mock the DB call so this stays a pure-function test.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.chat import ChatRequest  # noqa: E402


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
# Pydantic model
# =========================================================================

def test_chat_request_default_compare_mode_is_false():
    """Without the field, default to off — comparison is opt-in."""
    req = ChatRequest(message="hi")
    check("ChatRequest: compare_mode defaults to False",
          req.compare_mode is False)


def test_chat_request_explicit_compare_mode():
    req = ChatRequest(message="hi", compare_mode=True)
    check("ChatRequest: compare_mode=True round-trips", req.compare_mode is True)


# =========================================================================
# _build_citation_context — comparison_mode formatting
# =========================================================================

class _FakeRow:
    def __init__(self, id, title, original_filename):
        self._data = {"id": id, "title": title, "original_filename": original_filename}
    def __getitem__(self, key):
        return self._data[key]


class _FakeCursor:
    """Mimics the real aiosqlite cursor: `async with` + `await fetchall()`."""
    def __init__(self, rows):
        self._rows = list(rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetchall(self):
        return list(self._rows)


def _make_fake_get_db(title_by_id):
    """Return a function suitable for `patch.object(chat_mod, 'get_db', ...)`.

    The real `get_db` is an async-context-manager; callers do
    `async with get_db() as db:`. So our replacement must also be a
    callable that returns an awaitable that yields a context manager.
    """
    class _CursorCtx:
        """Async-context-manager wrapping a _FakeCursor — mimics aiosqlite's
        behavior of returning a context manager from `db.execute()`."""
        def __init__(self, cursor):
            self._cursor = cursor

        async def __aenter__(self):
            return self._cursor

        async def __aexit__(self, *a):
            return False

    class _Ctx:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def execute(self, sql, params):
            ids = params[:-1]  # last param is user_id
            rows = [
                _FakeRow(doc_id, title_by_id[doc_id], f"{doc_id}.pdf")
                for doc_id in ids if doc_id in title_by_id
            ]
            return _CursorCtx(_FakeCursor(rows))

    def _get_db():
        return _Ctx()
    return _get_db


def _make_empty_get_db():
    """Like _make_fake_get_db but returns no rows (document was deleted)."""
    class _CursorCtx:
        def __init__(self, cursor):
            self._cursor = cursor
        async def __aenter__(self): return self._cursor
        async def __aexit__(self, *a): return False

    class _EmptyCtx:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def execute(self, *a, **kw):
            return _CursorCtx(_FakeCursor([]))

    def _get_db():
        return _EmptyCtx()
    return _get_db


# Sample chunks — two docs, two chunks each. Each carries document_id
# in metadata (mimicking what build_rag_context returns).
SAMPLE_CHUNKS = [
    {
        "chunk_id": "c1",
        "content": "硅基流动成立于 2023 年，提供大模型 API 服务。",
        "metadata": {"user_id": "1", "document_id": "doc-a", "hierarchy_path": ""},
    },
    {
        "chunk_id": "c2",
        "content": "硅基流动的旗舰模型是 Qwen3-8B。",
        "metadata": {"user_id": "1", "document_id": "doc-a", "hierarchy_path": ""},
    },
    {
        "chunk_id": "c3",
        "content": "百炼由阿里云推出，主打 qwen-flash 模型。",
        "metadata": {"user_id": "1", "document_id": "doc-b", "hierarchy_path": ""},
    },
    {
        "chunk_id": "c4",
        "content": "百炼 API 与 OpenAI 兼容。",
        "metadata": {"user_id": "1", "document_id": "doc-b", "hierarchy_path": ""},
    },
]

# Documents our fake DB will know about.
FAKE_TITLES = {
    "doc-a": "硅基流动产品手册",
    "doc-b": "百炼 API 文档",
}


def test_context_str_no_doc_titles_by_default():
    """Without comparison_mode, the prompt context shouldn't mention doc names."""
    from app.api import chat as chat_mod
    import unittest.mock as _mock
    with _mock.patch.object(chat_mod, "get_db", _make_fake_get_db(FAKE_TITLES)):
        result = asyncio.run(chat_mod._build_citation_context(
            chunks=SAMPLE_CHUNKS, user_id=1,
        ))
    ctx = result["context_str"]
    check("context_str: no '(from: ...)' markers by default",
          "(from:" not in ctx)
    check("context_str: still includes [Context N] markers",
          "[Context 1]" in ctx and "[Context 4]" in ctx)


def test_context_str_includes_doc_titles_when_compare_mode():
    """With comparison_mode=True, every [Context N] block should lead with
    '(from: Doc Title)' so the LLM can attribute claims to a source."""
    from app.api import chat as chat_mod
    import unittest.mock as _mock
    with _mock.patch.object(chat_mod, "get_db", _make_fake_get_db(FAKE_TITLES)):
        result = asyncio.run(chat_mod._build_citation_context(
            chunks=SAMPLE_CHUNKS, user_id=1, comparison_mode=True,
        ))
    ctx = result["context_str"]
    check("context_str: includes (from: Doc Title) for chunk 1 (doc-a)",
          "[Context 1] (from: 硅基流动产品手册)" in ctx)
    check("context_str: includes (from: Doc Title) for chunk 3 (doc-b)",
          "[Context 3] (from: 百炼 API 文档)" in ctx)
    # The actual content of each chunk should still be present.
    check("context_str: chunk content is still present",
          "硅基流动成立于 2023" in ctx and "百炼由阿里云推出" in ctx)


def test_context_str_uses_untitled_when_doc_lookup_empty():
    """If the document isn't in our fake DB (edge case: doc was deleted),
    we should fall back to '(from: Untitled)' rather than blowing up."""
    from app.api import chat as chat_mod
    import unittest.mock as _mock
    with _mock.patch.object(chat_mod, "get_db", _make_empty_get_db()):
        result = asyncio.run(chat_mod._build_citation_context(
            chunks=SAMPLE_CHUNKS[:1], user_id=1, comparison_mode=True,
        ))
    ctx = result["context_str"]
    check("context_str: falls back to '(from: Untitled)' when doc is missing",
          "[Context 1] (from: Untitled)" in ctx)


def test_sources_array_unchanged_by_compare_mode():
    """The sources array (sent to the client) is independent of how the
    prompt context is formatted — it should be the same shape either way."""
    from app.api import chat as chat_mod
    import unittest.mock as _mock
    with _mock.patch.object(chat_mod, "get_db", _make_fake_get_db(FAKE_TITLES)):
        result_off = asyncio.run(chat_mod._build_citation_context(
            chunks=SAMPLE_CHUNKS, user_id=1,
        ))
        result_on = asyncio.run(chat_mod._build_citation_context(
            chunks=SAMPLE_CHUNKS, user_id=1, comparison_mode=True,
        ))
    check("sources: same length regardless of compare_mode",
          len(result_off["sources"]) == len(result_on["sources"]) == 4)
    check("sources: titles identical regardless of compare_mode",
          [s["title"] for s in result_off["sources"]]
          == [s["title"] for s in result_on["sources"]])
    check("sources: each still has document_id",
          all("document_id" in s for s in result_on["sources"]))


# =========================================================================
# LLM service signature
# =========================================================================

def test_llm_generate_rag_response_accepts_comparison_mode():
    """compare_mode is a kwarg with a default of False — backward compatible."""
    import inspect
    sig = inspect.signature(
        __import__("app.services.llm", fromlist=["LLMService"])
        .LLMService.generate_rag_response
    )
    check("LLMService.generate_rag_response has 'comparison_mode' kwarg",
          "comparison_mode" in sig.parameters)
    check("LLMService.generate_rag_response.comparison_mode defaults to False",
          sig.parameters["comparison_mode"].default is False)


# =========================================================================
# Driver
# =========================================================================

ALL_TESTS = [
    test_chat_request_default_compare_mode_is_false,
    test_chat_request_explicit_compare_mode,
    test_context_str_no_doc_titles_by_default,
    test_context_str_includes_doc_titles_when_compare_mode,
    test_context_str_uses_untitled_when_doc_lookup_empty,
    test_sources_array_unchanged_by_compare_mode,
    test_llm_generate_rag_response_accepts_comparison_mode,
]


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks for #9 cross-document comparison...")
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
