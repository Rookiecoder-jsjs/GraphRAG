"""Tests for the streaming-chat fixes:

- streaming generation now receives conversation history (used to be dropped)
- provider failures surface as a typed ``event: error`` SSE frame and the
  error text is never persisted to chat history
- unexpected generator failures still produce a terminal error frame
- should_reject intent streams a typed refusal instead of free-generating
- ChatRequest enforces a max message length
- rate limiter purges stale sprayed keys
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import deque
from pathlib import Path
from unittest import mock

import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("JWT_SECRET", "test-only-" + "k" * 48)
os.environ.setdefault("APP_ENV", "test")

from app.api import chat  # noqa: E402
from app.auth.rate_limit import SlidingWindowLimiter  # noqa: E402
from app.models.chat import ChatRequest  # noqa: E402
from pydantic import ValidationError  # noqa: E402


# ---------- fakes -----------------------------------------------------------

class _FakeCursor:
    """aiosqlite cursor stand-in.

    chat.py uses BOTH ``cur = await db.execute(...)`` and
    ``async with db.execute(...) as cur`` (aiosqlite cursors are awaitable
    AND async context managers) - the fake must support both forms.
    """

    def __init__(self, rows=None):
        self._rows = rows or []
        self.lastrowid = 1

    def __await__(self):
        yield
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetchall(self):
        return self._rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeDB:
    """Records every statement; serves canned rows for the history SELECT."""

    def __init__(self, history_rows=None):
        self.statements = []
        self._history = history_rows or []

    def execute(self, sql, params=()):
        self.statements.append((sql, tuple(params)))
        if sql.startswith("SELECT role, content"):
            return _FakeCursor(self._history)
        return _FakeCursor([])

    async def executemany(self, sql, params):
        self.statements.append((sql, tuple(params)))

    async def commit(self):
        pass


def _fake_get_db(db: _FakeDB):
    class _Ctx:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    return lambda: _Ctx()


class _FakeStreamLLM:
    def __init__(self, deltas):
        self._deltas = deltas
        self.captured_messages = []

    async def chat_complete_stream(self, messages, enable_thinking=None):
        self.captured_messages.append(messages)
        for kind, text in self._deltas:
            yield (kind, text)


def _collect(gen):
    async def _run():
        return [frame async for frame in gen]
    return asyncio.run(_run())


def _intent(value):
    async def _classify(query):
        return {"intent": value, "reason": "test"}
    return _classify


def _history_rows():
    # What the DESC-ordered SELECT returns: current (just-saved) user turn
    # first, then prior turns newest-first. The generator reverses this.
    return [
        {"role": "user", "content": "just-saved current turn"},
        {"role": "assistant", "content": "prev answer"},
        {"role": "user", "content": "prev question"},
    ]


def _run_stream(llm, db, intent="chitchat"):
    gen = chat._chat_stream_body(ChatRequest(message="what about it?"), 1, "c1")
    with mock.patch.object(chat, "get_db", _fake_get_db(db)), \
         mock.patch.object(chat, "classify_intent", _intent(intent)), \
         mock.patch.object(
             chat, "get_llm_service",
             mock.AsyncMock(return_value=llm),
         ):
        return _collect(gen)


# ---------- streaming fixes -------------------------------------------------

def test_stream_passes_history_to_generation():
    llm = _FakeStreamLLM([("content", "hello")])
    db = _FakeDB(_history_rows())
    frames = _run_stream(llm, db)

    msgs = llm.captured_messages[0]
    # system + prev question + prev answer + current user message
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert msgs[1]["content"] == "prev question"
    assert msgs[2]["content"] == "prev answer"
    assert msgs[-1]["content"] == "what about it?"
    assert frames[-1].startswith("event: done")


def test_stream_error_frame_and_error_text_never_saved():
    llm = _FakeStreamLLM([("content", "partial answer"), ("error", "boom")])
    db = _FakeDB([])
    frames = _run_stream(llm, db)

    assert any(f.startswith("event: error") for f in frames)
    assert not any(f.startswith("event: done") for f in frames)
    # The partial answer IS persisted, the error text is NOT.
    inserted = [p for sql, p in db.statements if "INSERT INTO messages" in sql]
    assert any("partial answer" in p for p in inserted)
    assert all("boom" not in str(p) for p in db.statements)


def test_stream_error_before_any_content_saves_nothing():
    llm = _FakeStreamLLM([("error", "boom")])
    db = _FakeDB([])
    frames = _run_stream(llm, db)

    assert any(f.startswith("event: error") for f in frames)
    inserts = [sql for sql, _ in db.statements if "INSERT INTO messages" in sql]
    # Only the user turn; no assistant placeholder row.
    assert len(inserts) == 1


def test_wrapper_emits_terminal_error_on_unexpected_failure():
    async def _boom_body(request, user_id, conversation_id):
        yield "data: {}\n\n"
        raise RuntimeError("kaboom")

    with mock.patch.object(chat, "_chat_stream_body", _boom_body):
        frames = _collect(
            chat.chat_stream_generator(ChatRequest(message="q"), 1, "c1")
        )
    assert frames[-1].startswith("event: error")
    assert "kaboom" in frames[-1]


def test_should_reject_streams_template_without_calling_llm():
    llm = _FakeStreamLLM([("content", "should never happen")])
    db = _FakeDB([])
    frames = _run_stream(llm, db, intent="should_reject")

    assert llm.captured_messages == []  # LLM never invoked
    # The chunk frame JSON-encodes the template (Chinese chars -> \uXXXX),
    # so decode before comparing.
    import json as _json
    chunk_text = "".join(
        _json.loads(f[len("data: "):])["chunk"]
        for f in frames if f.startswith("data: ")
    )
    assert chunk_text == chat._REJECTION_TEMPLATE
    assert frames[-1].startswith("event: done")
    # The refusal is persisted as an assistant turn.
    inserted = [p for sql, p in db.statements if "INSERT INTO messages" in sql]
    assert any(chat._REJECTION_TEMPLATE in p for p in inserted)


# ---------- request validation ---------------------------------------------

def test_chat_request_max_length():
    assert ChatRequest(message="x" * 8000).message
    with pytest.raises(ValidationError):
        ChatRequest(message="x" * 8001)


# ---------- rate limiter stale-key purge ------------------------------------

def test_rate_limiter_purges_stale_sprayed_keys():
    limiter = SlidingWindowLimiter(max_calls=5, window_seconds=60)
    # Simulate a key-spray: >10000 keys whose only hit expired long ago.
    stale = time.monotonic() - 120
    for i in range(10001):
        limiter._hits[f"spray:{i}"] = deque([stale])
    assert limiter.is_allowed("fresh:key") is True
    # Stale sprayed keys were dropped; only the live key remains.
    assert len(limiter._hits) < 100
    assert limiter.is_allowed("fresh:key") is True  # 2/5 - still allowed
