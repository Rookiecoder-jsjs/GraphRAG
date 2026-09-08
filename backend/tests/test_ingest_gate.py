"""Tests for the document ingestion concurrency gate (services/ingest_gate.py).

Covers: the gate is sized from config, it caps how many pipelines run at
once, and the upload wrapper (process_document_background) actually holds the
gate — a second document waits its turn instead of starting in parallel.
"""
import asyncio

import pytest

from app.services import ingest_gate as gate_mod
from app.services.ingest_gate import get_ingest_gate


@pytest.fixture(autouse=True)
def _fresh_gate():
    """Never leak a gate (or its acquired state) between tests."""
    gate_mod._gate = None
    yield
    gate_mod._gate = None


def test_gate_is_sized_from_config_and_shared():
    from app.config import get_settings

    gate = get_ingest_gate()
    assert isinstance(gate, asyncio.Semaphore)
    assert gate._value == get_settings().DOC_INGEST_CONCURRENCY
    # Process-wide singleton: repeated calls return the same object.
    assert get_ingest_gate() is gate


def test_reset_gate_builds_a_fresh_semaphore():
    first = get_ingest_gate()
    gate_mod.reset_ingest_gate()
    assert get_ingest_gate() is not first


def test_gate_caps_concurrent_holders_at_one():
    gate_mod._gate = asyncio.Semaphore(1)
    peak = 0
    inside = 0

    async def _work():
        nonlocal peak, inside
        async with get_ingest_gate():
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.01)
            inside -= 1

    async def main():
        await asyncio.gather(_work(), _work(), _work())

    asyncio.run(main())
    assert peak == 1  # never two holders at once


def test_gate_allows_up_to_its_limit():
    gate_mod._gate = asyncio.Semaphore(2)
    peak = 0
    inside = 0

    async def _work():
        nonlocal peak, inside
        async with get_ingest_gate():
            inside += 1
            peak = max(peak, inside)
            await asyncio.sleep(0.01)
            inside -= 1

    async def main():
        await asyncio.gather(_work(), _work(), _work())

    asyncio.run(main())
    assert peak == 2


def test_upload_pipeline_second_doc_waits_behind_the_gate(monkeypatch):
    import app.api.documents as docs

    gate_mod._gate = asyncio.Semaphore(1)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    entered: list = []

    async def fake_pipeline(doc_id, user_id, markdown, title):
        entered.append(doc_id)  # reached the pipeline => gate was acquired
        if doc_id == "doc-a":
            first_started.set()
            await release_first.wait()

    monkeypatch.setattr(docs, "_run_ingest_pipeline", fake_pipeline)

    async def main():
        t1 = asyncio.create_task(
            docs.process_document_background("doc-a", 1, "md", "A")
        )
        await first_started.wait()  # doc-a now holds the gate
        t2 = asyncio.create_task(
            docs.process_document_background("doc-b", 1, "md", "B")
        )
        await asyncio.sleep(0.02)
        assert entered == ["doc-a"]  # doc-b is queued, not started
        release_first.set()
        await asyncio.wait_for(asyncio.gather(t1, t2), timeout=2)
        assert entered == ["doc-a", "doc-b"]

    asyncio.run(main())
