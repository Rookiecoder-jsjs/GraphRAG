"""Tests for bounded conversation-history loading + compaction (T2-2).

Covers the GUIDE-003 acceptance list:
  - threshold trigger folds old turns into one summary row
  - idempotence: repeated compaction below threshold is a no-op
  - second compaction MERGES the old summary (one-live-summary invariant)
  - loader assembly order: [summary?, ...recent] chronological
  - summary only rides along when the window doesn't cover it
  - transcript endpoint filters role='summary' rows
  - LLM failure leaves history untouched (fire-and-forget contract)

Uses real SQLite files (tmp_path) — the SQL semantics under test (id
monotonicity, DESC windows) are exactly what fakes would paper over.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types
from pathlib import Path
from unittest import mock

import aiosqlite
import pytest

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("JWT_SECRET", "test-only-" + "k" * 48)
os.environ.setdefault("APP_ENV", "test")

from app.services import history  # noqa: E402

CONV = "c-test"


# ---------- harness ----------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


async def _connect(tmp_path):
    db = await aiosqlite.connect(str(Path(tmp_path) / "hist.db"))
    db.row_factory = aiosqlite.Row
    await db.execute(_SCHEMA)
    await db.commit()
    return db


async def _seed(db, n, prefix="m"):
    """Insert ``n`` alternating user/assistant turns; returns last id."""
    for i in range(1, n + 1):
        role = "user" if i % 2 else "assistant"
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (CONV, role, f"{prefix}-{i}"),
        )
    await db.commit()


def _settings(**overrides):
    base = dict(
        HISTORY_COMPACT_ENABLED=True,
        HISTORY_WINDOW_LIMIT=10,
        HISTORY_COMPACT_THRESHOLD=24,
        HISTORY_KEEP_RECENT=6,
        HISTORY_SUMMARY_MAX_CHARS=400,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _ctx_factory(db_holder):
    class _Ctx:
        async def __aenter__(self):
            self._db = db_holder[0]
            return self._db

        async def __aexit__(self, *exc):
            return False

    return lambda: _Ctx()


async def _count(db, where="role != 'summary'", args=()):
    if where:
        where = "AND " + where
    async with db.execute(
        f"SELECT COUNT(*) FROM messages WHERE conversation_id = ? {where}",
        (CONV, *args),
    ) as cur:
        return (await cur.fetchone())[0]


# ---------- loader -----------------------------------------------------------

@pytest.mark.asyncio
async def test_loader_chronological_and_bounded(tmp_path):
    db = await _connect(tmp_path)
    try:
        await _seed(db, 12)
        out = await history.load_chat_history(
            db, CONV, limit=10, include_summary=False,
        )
        assert len(out) == 10
        # Chronological: oldest of the kept window first, newest last.
        assert out[0]["content"] == "m-3"
        assert out[-1]["content"] == "m-12"
        assert [e["role"] for e in out[:2]] == ["user", "assistant"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_loader_empty_conversation(tmp_path):
    db = await _connect(tmp_path)
    try:
        out = await history.load_chat_history(db, CONV, limit=10)
        assert out == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_summary_rides_when_window_cannot_cover_it(tmp_path):
    db = await _connect(tmp_path)
    try:
        # ids: 1..4 raw, 5 summary(fold of 1..4), 6..9 raw
        await _seed(db, 4, "old")
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (CONV, history.SUMMARY_ROLE, "fold-of-old"),
        )
        await _seed_from(db, 5, 4, "new")
        await db.commit()

        # Window covers everything -> raw turns strictly better than their
        # own fold; summary must NOT ride along.
        full = await history.load_chat_history(db, CONV, limit=10)
        assert len(full) == 8
        assert all("摘要" not in e["content"] for e in full)

        # Window of 2 reaches back only over ids 8..9; the summary (id 5)
        # is older than the loaded window and rides in front as system.
        tail = await history.load_chat_history(db, CONV, limit=2)
        assert [e["role"] for e in tail] == ["system", "user", "assistant"]
        assert tail[0]["content"] == "（此前对话的摘要）fold-of-old"
        assert tail[1]["content"] == "new-7" and tail[2]["content"] == "new-8"
    finally:
        await db.close()


async def _seed_from(db, start_index, n, prefix):
    for i in range(start_index, start_index + n):
        role = "user" if i % 2 else "assistant"
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (CONV, role, f"{prefix}-{i}"),
        )


@pytest.mark.asyncio
async def test_include_summary_false_never_yields_one(tmp_path):
    db = await _connect(tmp_path)
    try:
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (?, ?, ?)",
            (CONV, history.SUMMARY_ROLE, "s"),
        )
        await _seed(db, 2)
        out = await history.load_chat_history(db, CONV, limit=1, include_summary=False)
        assert [e["role"] for e in out] == ["assistant"]
    finally:
        await db.close()


# ---------- compaction -------------------------------------------------------

def _patch_history_env(settings, summarize_text="SUMMARY-OK", fail=False):
    """Patch settings + the LLM summarizer inside services.history."""
    calls = {"transcripts": []}

    async def _fake_summarize(transcript):
        if fail:
            raise RuntimeError("llm down")
        calls["transcripts"].append(transcript)
        return summarize_text

    return (
        mock.patch.object(history, "get_settings", lambda: settings),
        mock.patch.object(history, "_summarize", _fake_summarize),
        calls,
    )


@pytest.mark.asyncio
async def test_threshold_trigger_folds_keeps_recent(tmp_path, monkeypatch):
    db = await _connect(tmp_path)
    holder = [db]
    try:
        await _seed(db, 30)  # > threshold 24
        p1, p2, calls = _patch_history_env(_settings())
        with p1, p2:
            monkeypatch.setattr(
                "app.database.get_db", _ctx_factory(holder)
            )
            await history.maybe_compact_history(CONV)

        # 30 folded down to KEEP_RECENT=6 raw + exactly one summary row.
        assert await _count(db) == 6
        assert await _count(db, "role = 'summary'") == 1
        async with db.execute(
            "SELECT content FROM messages WHERE role='summary'"
        ) as cur:
            assert (await cur.fetchone())["content"] == "SUMMARY-OK"
        # Kept rows are the NEWEST six.
        async with db.execute(
            "SELECT content FROM messages WHERE role != 'summary' ORDER BY id"
        ) as cur:
            contents = [r["content"] for r in await cur.fetchall()]
        assert contents == [f"m-{i}" for i in range(25, 31)]
        # Transcript fed to the summarizer started at the oldest turn
        # (rendered as "role: content" blocks) and stops before KEEP_RECENT.
        assert calls["transcripts"][0].startswith("user: m-1")
        assert "m-24" in calls["transcripts"][0]
        assert "m-25" not in calls["transcripts"][0]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_below_threshold_is_noop(tmp_path, monkeypatch):
    db = await _connect(tmp_path)
    holder = [db]
    try:
        await _seed(db, 10)
        before = await _count(db, "")
        p1, p2, calls = _patch_history_env(_settings())
        with p1, p2:
            monkeypatch.setattr("app.database.get_db", _ctx_factory(holder))
            await history.maybe_compact_history(CONV)
        assert await _count(db, "") == before
        assert calls["transcripts"] == []  # LLM never invoked
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_llm_failure_leaves_history_intact(tmp_path, monkeypatch):
    db = await _connect(tmp_path)
    holder = [db]
    try:
        await _seed(db, 30)
        p1, p2, _ = _patch_history_env(_settings(), fail=True)
        with p1, p2:
            monkeypatch.setattr("app.database.get_db", _ctx_factory(holder))
            await history.maybe_compact_history(CONV)  # must not raise

        assert await _count(db, "") == 30
        assert await _count(db, "role = 'summary'") == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_second_compaction_merges_old_summary(tmp_path, monkeypatch):
    db = await _connect(tmp_path)
    holder = [db]
    try:
        await _seed(db, 30, "r1")
        p1, p2, calls = _patch_history_env(_settings(), summarize_text="OLD")
        with p1, p2:
            monkeypatch.setattr("app.database.get_db", _ctx_factory(holder))
            await history.maybe_compact_history(CONV)

        # Segment regrows past the threshold again.
        await _seed_from(db, 100, 25, "r2")
        await db.commit()
        p3, p4, calls2 = _patch_history_env(_settings(), summarize_text="NEW")
        with p3, p4:
            monkeypatch.setattr("app.database.get_db", _ctx_factory(holder))
            await history.maybe_compact_history(CONV)

        # One-live-summary invariant holds after the merge.
        assert await _count(db, "role = 'summary'") == 1
        async with db.execute(
            "SELECT content FROM messages WHERE role='summary'"
        ) as cur:
            assert (await cur.fetchone())["content"] == "NEW"
        # The merge transcript carried the OLD summary forward.
        assert "（更早的旧摘要）\nOLD" in calls2["transcripts"][0]
        # Raw backlog stays bounded at KEEP_RECENT.
        assert await _count(db) == 6
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_disabled_setting_short_circuits(tmp_path, monkeypatch):
    db = await _connect(tmp_path)
    holder = [db]
    try:
        await _seed(db, 40)
        p1, p2, calls = _patch_history_env(
            _settings(HISTORY_COMPACT_ENABLED=False)
        )
        with p1, p2:
            monkeypatch.setattr("app.database.get_db", _ctx_factory(holder))
            await history.maybe_compact_history(CONV)
            assert history.spawn_history_compaction(CONV) is None
        assert await _count(db, "") == 40
        assert calls["transcripts"] == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_spawn_returns_live_task(tmp_path, monkeypatch):
    db = await _connect(tmp_path)
    holder = [db]
    await _seed(db, 5)
    p1, p2, calls = _patch_history_env(_settings())
    with p1, p2:
        monkeypatch.setattr("app.database.get_db", _ctx_factory(holder))
        task = history.spawn_history_compaction(CONV)
        assert isinstance(task, asyncio.Task)
        await task  # completes without raising
    # Below threshold: no LLM call happened, task finished cleanly.
    assert calls["transcripts"] == []
    await db.close()


@pytest.mark.asyncio
async def test_summary_max_chars_enforced(tmp_path, monkeypatch):
    """_summarize clamps an over-long LLM reply to HISTORY_SUMMARY_MAX_CHARS.

    Patched at the LLM boundary (services.llm.get_llm_service) so the REAL
    _summarize — including its clamp — runs.
    """
    db = await _connect(tmp_path)
    holder = [db]
    try:
        await _seed(db, 30)

        from app.services import llm as llm_mod

        class _FakeLLM:
            async def chat_complete(self, messages, **kwargs):
                return "x" * 500  # simulate an over-verbose LLM reply

        p1, _, _ = _patch_history_env(_settings(HISTORY_SUMMARY_MAX_CHARS=400))
        with p1:
            monkeypatch.setattr("app.database.get_db", _ctx_factory(holder))
            monkeypatch.setattr(
                llm_mod, "get_llm_service",
                mock.AsyncMock(return_value=_FakeLLM()),
            )
            await history.maybe_compact_history(CONV)

        async with db.execute(
            "SELECT content FROM messages WHERE role='summary'"
        ) as cur:
            stored = (await cur.fetchone())["content"]
        assert len(stored) == 400
    finally:
        await db.close()


# ---------- endpoint filter --------------------------------------------------

@pytest.mark.asyncio
async def test_transcript_endpoint_filters_summary_rows(monkeypatch):
    """GET /conversations/{id}/messages never exposes role='summary'."""
    from app.api import chat as chat_api

    captured = {}

    class _Cur:
        def __init__(self, rows, filter_role=None):
            self._rows = rows
            self._filter_role = filter_role

        def __await__(self):
            yield
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def fetchone(self):
            return self._rows[0] if self._rows else None

        async def fetchall(self):
            # Honor the role!='summary' bind param so the assertion exercises
            # the same semantics as SQLite instead of a canned row list.
            if self._filter_role is not None:
                return [r for r in self._rows if r["role"] != self._filter_role]
            return self._rows

    class _DB:
        def execute(self, sql, params=()):
            captured["sql"] = sql
            if "FROM conversations" in sql:
                return _Cur([{"id": CONV}], None)  # ownership check passes
            filter_role = (
                params[1] if ("role != ?" in sql and len(params) >= 2) else None
            )
            return _Cur([
                {"id": 1, "role": "user", "content": "q"},
                {"id": 2, "role": history.SUMMARY_ROLE, "content": "s"},
            ], filter_role)

    class _Ctx:
        async def __aenter__(self):
            return _DB()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(chat_api, "get_db", lambda: _Ctx())
    out = await chat_api.get_conversation_messages(CONV, {"id": 1})
    assert [m["role"] for m in out] == ["user"]
    assert "role != ?" in captured["sql"]
