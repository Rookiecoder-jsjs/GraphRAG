"""Tests for the streaming-chat fixes:

- streaming generation now receives conversation history (used to be dropped)
- provider failures surface as a typed ``event: error`` SSE frame and the
  error text is never persisted to chat history
- unexpected generator failures still produce a terminal error frame
- should_reject intent streams a typed refusal instead of free-generating
- chitchat intent uses a dedicated light prompt (no <context>, no citations)
- chat_complete normalizes content=null to "" and only retries retryable
  HTTP errors (4xx fails fast, no blind retry)
- ChatRequest enforces a max message length
- rate limiter purges stale sprayed keys
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections import deque
from pathlib import Path
from unittest import mock

import httpx
import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("JWT_SECRET", "test-only-" + "k" * 48)
os.environ.setdefault("APP_ENV", "test")

from app.api import chat  # noqa: E402
from app.auth.rate_limit import SlidingWindowLimiter  # noqa: E402
from app.models.chat import ChatRequest  # noqa: E402
from app.services.llm import LLMService  # noqa: E402
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

    async def chat_complete_stream(
        self, messages, enable_thinking=None, max_tokens=None
    ):
        self.captured_messages.append(messages)
        for kind, text in self._deltas:
            yield (kind, text)


class _FakeChatLLM:
    """Records non-streaming chat_complete calls; returns a canned answer."""

    def __init__(self, response="canned"):
        self._response = response
        self.captured_messages = []

    async def chat_complete(
        self, messages, model=None, temperature=None, max_tokens=None, stream=False
    ):
        self.captured_messages.append(messages)
        return self._response


class _FakeResponse:
    """httpx.Response stand-in: json() returns canned data, raise_for_status
    honors the status code."""

    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.request = httpx.Request("POST", "http://test/chat/completions")
        self.text = json.dumps(data) if data is not None else ""

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=self.request, response=self
            )


class _FakeHTTPClient:
    """Records post calls; each call pops the next outcome, which is either a
    _FakeResponse to return or an Exception to raise (last one reused)."""

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    async def post(self, url, headers=None, json=None):
        outcome = (
            self._outcomes[self.calls]
            if self.calls < len(self._outcomes)
            else self._outcomes[-1]
        )
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _make_llm_service(client) -> LLMService:
    """An LLMService wired to the fake HTTP client (skips network + key check)."""
    svc = LLMService()
    svc.api_key = "test-key"
    svc._client = client
    return svc


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


# ---------- chitchat intent: dedicated light prompt --------------------------

def test_stream_chitchat_uses_light_prompt_without_context():
    llm = _FakeStreamLLM([("content", "你好！")])
    db = _FakeDB([])
    frames = _run_stream(llm, db, intent="chitchat")

    msgs = llm.captured_messages[0]
    assert msgs[0]["role"] == "system"
    system_prompt = msgs[0]["content"]
    # No <context> block and no citation instruction on the chitchat prompt.
    assert "<context>" not in system_prompt
    assert "CITATIONS" not in system_prompt
    assert msgs[-1]["role"] == "user"
    assert msgs[-1]["content"] == "what about it?"
    # No sources event for chitchat; the turn still ends with done.
    assert not any(f.startswith("event: sources") for f in frames)
    assert frames[-1].startswith("event: done")


def test_non_streaming_chitchat_uses_light_prompt():
    llm = _FakeChatLLM("你好！")
    db = _FakeDB([])
    with mock.patch.object(chat, "get_db", _fake_get_db(db)), \
         mock.patch.object(chat, "classify_intent", _intent("chitchat")), \
         mock.patch.object(chat, "get_llm_service", mock.AsyncMock(return_value=llm)):
        result = asyncio.run(chat.chat(ChatRequest(message="你好"), {"id": 1}))

    msgs = llm.captured_messages[0]
    assert msgs[0]["role"] == "system"
    assert "<context>" not in msgs[0]["content"]
    assert "CITATIONS" not in msgs[0]["content"]
    assert msgs[-1]["role"] == "user"
    assert result["message"] == "你好！"
    assert result["sources"] == []
    assert result["citation_coverage"] == 0.0
    # The chitchat reply is persisted as one assistant turn.
    inserted = [p for sql, p in db.statements if "INSERT INTO messages" in sql]
    assert any("你好！" in p for p in inserted)


# ---------- chat_complete robustness (content=null, retry policy) ------------

def test_chat_complete_content_null_returns_empty_string():
    client = _FakeHTTPClient([
        _FakeResponse({"choices": [{"message": {"content": None}}]}),
    ])
    svc = _make_llm_service(client)

    result = asyncio.run(svc.chat_complete([{"role": "user", "content": "hi"}]))
    assert result == ""
    assert client.calls == 1  # a successful response is never retried


def test_chat_complete_4xx_not_retried():
    client = _FakeHTTPClient([_FakeResponse({}, status_code=400)])
    svc = _make_llm_service(client)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(svc.chat_complete([{"role": "user", "content": "hi"}]))
    assert client.calls == 1  # 400 fails fast, no retry


def test_chat_complete_retries_once_on_retryable_status():
    client = _FakeHTTPClient([
        _FakeResponse({}, status_code=503),
        _FakeResponse({"choices": [{"message": {"content": "ok"}}]}),
    ])
    svc = _make_llm_service(client)

    result = asyncio.run(svc.chat_complete([{"role": "user", "content": "hi"}]))
    assert result == "ok"
    assert client.calls == 2  # one retry succeeded


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
