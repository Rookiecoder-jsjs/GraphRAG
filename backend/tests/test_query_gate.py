"""Tests for app.services.query_gate — the retrieval admission gate (ADR-009).

Policy under test: queue up to QUERY_MAX_QUEUE_SECONDS, then reject. Accepted
retrievals run the full-quality pipeline; rejected ones surface as
search → 429 + Retry-After and chat SSE → terminal ``event: error``.

Cancel-safety matters because a waiter cancelled mid-queue must never
consume a slot (Python 3.11's asyncio.Semaphore loses permits on cancel —
the gate is built on a Condition counter to avoid exactly that, and these
tests pin the behaviour).
"""
import asyncio
import json
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app.services import query_gate as query_gate_mod
from app.services import retriever as retriever_mod
from app.services.query_gate import QueryGateTimeout


@pytest.fixture(autouse=True)
def _fresh_gate():
    query_gate_mod._gate = None
    yield
    query_gate_mod._gate = None


# ---------------------------------------------------------------- gate unit

@pytest.mark.asyncio
async def test_peak_concurrency_bounded():
    gate = query_gate_mod._QueryGate(limit=2, max_queue_seconds=5.0)
    peak = current = 0

    async def worker():
        nonlocal peak, current
        async with gate.slot():
            current += 1
            peak = max(peak, current)
            await asyncio.sleep(0.01)
            current -= 1

    results = await asyncio.gather(*[worker() for _ in range(8)])
    assert results == [None] * 8
    assert peak == 2, f"peak {peak} != limit 2"
    assert gate.active == 0


@pytest.mark.asyncio
async def test_queue_timeout_raises_with_retry_after():
    gate = query_gate_mod._QueryGate(limit=1, max_queue_seconds=0.05)
    async with gate.slot():
        with pytest.raises(QueryGateTimeout) as exc_info:
            await gate.acquire()
        assert exc_info.value.retry_after >= 1
        assert "查询压力" in str(exc_info.value)


@pytest.mark.asyncio
async def test_rejected_request_does_not_leak_slot():
    gate = query_gate_mod._QueryGate(limit=1, max_queue_seconds=0.05)
    async with gate.slot():
        with pytest.raises(QueryGateTimeout):
            await gate.acquire()
    assert gate.active == 0
    # The gate still admits after rejections.
    async with gate.slot():
        assert gate.active == 1


@pytest.mark.asyncio
async def test_cancelled_waiters_do_not_leak_slots():
    gate = query_gate_mod._QueryGate(limit=2, max_queue_seconds=5.0)
    admitted = 0

    async def worker():
        nonlocal admitted
        async with gate.slot():
            admitted += 1
            await asyncio.sleep(0.01)

    tasks = [asyncio.create_task(worker()) for _ in range(10)]
    await asyncio.sleep(0.005)  # 2 admitted, 8 queued
    for t in tasks[2:7]:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    assert gate.active == 0, "cancelled waiters must not consume slots"
    # A fresh acquisition succeeds instantly (slot was never leaked).
    await asyncio.wait_for(gate.acquire(), timeout=0.1)
    await gate.release()


# ------------------------------------------------- endpoint wiring (search)

def test_search_maps_gate_timeout_to_429(monkeypatch):
    from app.api.auth import get_current_user
    from app.main import app

    async def _reject(*args, **kwargs):
        raise QueryGateTimeout(retry_after=7)

    monkeypatch.setattr(retriever_mod, "retrieve", _reject)
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    try:
        resp = TestClient(app).post(
            "/api/search", json={"query": "知识图谱", "top_k": 5}
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert resp.status_code == 429
    assert resp.headers.get("retry-after") == "7"
    assert "查询压力" in resp.json()["detail"]


# --------------------------------------------------- endpoint wiring (chat)

def test_chat_stream_rejected_by_gate_emits_busy_error_frame(monkeypatch):
    """A gate rejection during retrieval surfaces as a terminal `event: error`
    carrying the busy message — and never a done frame (reuse of the
    test_chat_fixes streaming fakes; retrieval is patched at the source)."""
    from test_chat_fixes import (
        _FakeDB,
        _FakeStreamLLM,
        _collect,
        _fake_get_db,
        _intent,
    )
    from app.api import chat
    from app.models.chat import ChatRequest

    async def _reject(*args, **kwargs):
        raise QueryGateTimeout(retry_after=7)

    monkeypatch.setattr(retriever_mod, "retrieve", _reject)
    monkeypatch.setattr("app.services.query_processor.get_llm_service",
                        mock.AsyncMock(), raising=False)

    db = _FakeDB([])
    gen = chat._chat_stream_body(
        ChatRequest(message="知识图谱检索服务是谁负责"), 1, "c1"
    )
    with mock.patch.object(chat, "get_db", _fake_get_db(db)), \
         mock.patch.object(chat, "classify_intent", _intent("fact_retrieval")), \
         mock.patch.object(chat, "get_llm_service",
                           mock.AsyncMock(return_value=_FakeStreamLLM([]))):
        frames = _collect(gen)

    error_frames = [f for f in frames if f.startswith("event: error")]
    assert error_frames, "expected a terminal busy error frame"
    payload = json.loads(error_frames[-1].split("data: ", 1)[1])
    assert "查询压力" in payload["error"]
    assert not any(f.startswith("event: done") for f in frames)
