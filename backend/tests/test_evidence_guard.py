"""Tests for the evidence guard (FEAT-027, CRAG-lite).

When the reranker says every retrieved chunk is below the relevance floor,
generating anyway is how hallucinations happen. The guard makes chat answer
with a static insufficient-evidence template instead — sources are still
returned/saved so the user can see what WAS found (and why it wasn't enough).

Semantics pinned here:
  * grade_evidence pure function: empty → low; no numeric relevance_score
    (rerank failed → RRF fallback) → unknown → behave as before; max score
    ≥ floor → normal.
  * Guard ON + low → NO LLM call, static message, evidence_level="low",
    sources intact, assistant message persisted (both chat paths).
  * Guard OFF or unknown or normal → exactly today's behaviour, and the
    response carries no evidence_level key at all.
"""
import asyncio
import json
from unittest import mock

import httpx
import pytest

from app.services.llm import LLMService


class _FakeHTTPResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.request = httpx.Request("POST", "http://test/chat/completions")
        self.text = json.dumps(data)

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=self.request, response=self
            )


class _FakeHTTPClient:
    async def post(self, url, headers=None, json=None):
        return _FakeHTTPResponse(
            {"choices": [{"message": {"content": "canned"}}]}
        )


def _canned_llm_service() -> LLMService:
    svc = LLMService()
    svc.api_key = "test-key"
    svc._client = _FakeHTTPClient()
    return svc


# =========================================================================
# Pure grading
# =========================================================================

def test_grade_empty_chunks_is_low():
    from app.services.evidence import grade_evidence

    assert grade_evidence([], floor=0.30) == ("low", 0.0)


def test_grade_missing_scores_is_unknown():
    from app.services.evidence import grade_evidence

    chunks = [{"chunk_id": "c1", "content": "x"}, {"chunk_id": "c2"}]
    assert grade_evidence(chunks, floor=0.30) == ("unknown", 0.0)


def test_grade_below_floor_is_low():
    from app.services.evidence import grade_evidence

    chunks = [{"relevance_score": 0.1}, {"relevance_score": 0.29}]
    assert grade_evidence(chunks, floor=0.30) == ("low", 0.29)


def test_grade_at_or_above_floor_is_normal():
    from app.services.evidence import grade_evidence

    chunks = [{"relevance_score": 0.1}, {"relevance_score": 0.30}]
    assert grade_evidence(chunks, floor=0.30) == ("normal", 0.30)


# =========================================================================
# Template registration
# =========================================================================

def test_insufficient_evidence_template_registered_and_renders():
    from app.prompts import load_prompt
    from app.prompts.templates import TEMPLATE_NAMES

    assert "insufficient_evidence" in TEMPLATE_NAMES
    text = load_prompt("insufficient_evidence")
    assert isinstance(text, str) and len(text) > 40


# =========================================================================
# Handler harness (pattern from test_chat_fixes.py)
# =========================================================================

class _FakeDB:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        return _FakeCursor([])

    async def executemany(self, sql, params):
        self.statements.append((sql, tuple(params)))

    async def commit(self):
        pass


class _FakeCursor:
    """Stands in for an aiosqlite cursor, which supports BOTH
    ``await db.execute(...)`` and ``async with db.execute(...)``."""

    def __init__(self, rows):
        self._rows = rows
        self.lastrowid = 1

    def __await__(self):
        async def _self():
            return self
        return _self().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._rows:
            raise StopAsyncIteration
        return self._rows.pop(0)


def _fake_get_db(db):
    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    return lambda: _Ctx()


def _intent(value):
    async def _classify(query):
        return {"intent": value, "reason": "test"}
    return _classify


def _chunk(score=None, chunk_id="c1", doc="doc-1"):
    c = {"chunk_id": chunk_id, "content": "资料内容", "metadata": {"document_id": doc}}
    if score is not None:
        c["relevance_score"] = score
    return c


def _low_context():
    return {"chunks": [_chunk(0.05), _chunk(0.12, chunk_id="c2")],
            "entities": [], "relations": []}


def _collect(gen):
    async def _run():
        return [frame async for frame in gen]
    return asyncio.run(_run())


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "evidence_test.db"))
    from app.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _run_chat_nonstreaming(db, context, *, llm_spy):
    from app.api import chat as chat_mod
    from app.models.chat import ChatRequest

    async def fake_retrieve(*args, **kwargs):
        return context

    with mock.patch.object(chat_mod, "get_db", _fake_get_db(db)), \
         mock.patch.object(chat_mod, "classify_intent", _intent("fact_retrieval")), \
         mock.patch("app.services.retriever.retrieve", fake_retrieve), \
         mock.patch.object(chat_mod, "get_llm_service", llm_spy):
        return asyncio.run(chat_mod.chat(ChatRequest(message="语料外的问题"), {"id": 1}))


# =========================================================================
# Non-streaming POST /api/chat
# =========================================================================

def test_nonstreaming_low_evidence_skips_llm():
    from app.prompts import load_prompt

    db = _FakeDB()
    llm_spy = mock.AsyncMock(return_value=LLMService())
    result = _run_chat_nonstreaming(db, _low_context(), llm_spy=llm_spy)

    llm_spy.assert_not_called()  # the whole point: no generation from junk
    assert result["evidence_level"] == "low"
    assert result["message"] == load_prompt("insufficient_evidence")
    assert result["citation_coverage"] == 0.0
    # No conversation_id in the request → the handler mints a fresh one.
    assert result["conversation_id"]
    # Sources still surface the (weak) references so the user can judge.
    assert len(result["sources"]) == 2
    # The assistant turn is persisted with the guard text, not LLM output.
    saved = [p for sql, p in db.statements
             if "INSERT INTO messages" in sql and p and p[1] == "assistant"]
    assert saved, "assistant turn must be persisted"


def test_nonstreaming_normal_evidence_unchanged():
    db = _FakeDB()
    llm_spy = mock.AsyncMock(return_value=_canned_llm_service())
    context = {"chunks": [_chunk(0.9)], "entities": [], "relations": []}
    result = _run_chat_nonstreaming(db, context, llm_spy=llm_spy)

    llm_spy.assert_called_once()
    assert "evidence_level" not in result
    assert result["message"] == "canned"


def test_nonstreaming_unknown_evidence_unchanged():
    """rerank failed → no scores → 'unknown' must NOT trigger the guard."""
    db = _FakeDB()
    llm_spy = mock.AsyncMock(return_value=_canned_llm_service())
    context = {"chunks": [_chunk(None), _chunk(None, chunk_id="c2")],
               "entities": [], "relations": []}
    result = _run_chat_nonstreaming(db, context, llm_spy=llm_spy)

    llm_spy.assert_called_once()
    assert "evidence_level" not in result


def test_nonstreaming_guard_disabled_keeps_generation():
    db = _FakeDB()
    llm_spy = mock.AsyncMock(return_value=_canned_llm_service())
    import app.config as config_mod

    config_mod.get_settings.cache_clear()
    try:
        import os
        os.environ["ENABLE_EVIDENCE_GUARD"] = "false"
        config_mod.get_settings.cache_clear()
        result = _run_chat_nonstreaming(db, _low_context(), llm_spy=llm_spy)
    finally:
        os.environ.pop("ENABLE_EVIDENCE_GUARD", None)
        config_mod.get_settings.cache_clear()

    llm_spy.assert_called_once()
    assert "evidence_level" not in result


# =========================================================================
# Streaming POST /api/chat/stream
# =========================================================================

def _run_stream(context, *, llm_spy):
    from app.api import chat as chat_mod
    from app.models.chat import ChatRequest

    async def fake_retrieve(*args, **kwargs):
        return context

    db = _FakeDB()
    gen = chat_mod._chat_stream_body(ChatRequest(message="语料外的问题"), 1, "c1")
    with mock.patch.object(chat_mod, "get_db", _fake_get_db(db)), \
         mock.patch.object(chat_mod, "classify_intent", _intent("fact_retrieval")), \
         mock.patch("app.services.retriever.retrieve", fake_retrieve), \
         mock.patch.object(chat_mod, "get_llm_service", llm_spy):
        frames = _collect(gen)
    return frames, db


def test_stream_low_evidence_frames_in_order_without_llm():
    from app.prompts import load_prompt

    llm_spy = mock.AsyncMock(return_value=LLMService())
    frames, db = _run_stream(_low_context(), llm_spy=llm_spy)

    llm_spy.assert_not_called()
    events = [f.split("\n", 1)[0] for f in frames if f.startswith("event:")]
    assert "event: sources" in events  # references still delivered first
    assert events[-1] == "event: done"

    done_frame = next(f for f in frames if f.startswith("event: done"))
    payload = json.loads(done_frame.split("data: ", 1)[1])
    assert payload["evidence_level"] == "low"
    assert payload["citation_coverage"] == 0.0
    assert len(payload["sources"]) == 2

    text_frame = next(f for f in frames if f.startswith("data: "))
    chunk_text = json.loads(text_frame.split("data: ", 1)[1])["chunk"]
    assert chunk_text == load_prompt("insufficient_evidence")

    saved = [p for sql, p in db.statements
             if "INSERT INTO messages" in sql and p and p[1] == "assistant"]
    assert saved, "assistant turn must be persisted"


def test_stream_normal_evidence_streams_llm_output():
    class _StreamLLM:
        async def chat_complete_stream(self, messages, enable_thinking=None, max_tokens=None):
            yield ("content", "正常回答")

    llm_spy = mock.AsyncMock(return_value=_StreamLLM())
    context = {"chunks": [_chunk(0.9)], "entities": [], "relations": []}
    frames, _db = _run_stream(context, llm_spy=llm_spy)

    llm_spy.assert_called_once()
    done_frame = next(f for f in frames if f.startswith("event: done"))
    payload = json.loads(done_frame.split("data: ", 1)[1])
    assert "evidence_level" not in payload
