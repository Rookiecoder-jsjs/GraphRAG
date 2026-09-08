"""Tests for progress SSE on SQLite polling (api/progress.py + progress_tracker.py).

Covers: the payload_json column exists after init_db and survives a second
init_db (idempotent ALTER); emit_and_save -> get_rows_since round-trips rich
payloads verbatim; legacy rows (NULL payload) degrade via _event_from_row;
and the SSE event stream replays history then terminates on complete/error —
while a consumer already polling picks up rows written mid-stream (the tail).
"""
import asyncio
import json

import pytest

from app.config import get_settings
from app.database import get_db, init_db
from app.services.progress_tracker import ProgressEmitter


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    """Give every test a throwaway SQLite file with a fresh schema."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "progress_test.db"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _bootstrap(user_id: int = 1) -> None:
    await init_db()
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) "
            "VALUES (?, ?, ?)",
            (user_id, f"u{user_id}", "x"),
        )
        await db.execute(
            "INSERT OR IGNORE INTO documents (id, user_id, title, status) "
            "VALUES ('doc-1', ?, 'title', 'pending')",
            (user_id,),
        )
        await db.commit()


def _run(coro):
    asyncio.run(coro)


def _parse_frames(frames):
    events = []
    for f in frames:
        payload = f.split("data: ", 1)[1].strip()
        events.append(json.loads(payload))
    return events


def test_payload_column_and_index_exist_and_init_is_idempotent():
    async def main():
        await _bootstrap()
        async with get_db() as db:
            async with db.execute("PRAGMA table_info(progress_history)") as cur:
                cols = {r["name"] for r in await cur.fetchall()}
            assert "payload_json" in cols
            async with db.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND name = 'idx_progress_history_doc'"
            ) as cur:
                assert await cur.fetchone() is not None
        # A second init_db must not blow up on the guarded ALTER.
        await init_db()

    _run(main())


def test_emit_and_save_roundtrips_rich_payload():
    async def main():
        await _bootstrap()
        em = ProgressEmitter()
        entities = ["人工智能", "图数据库", "ACME-42"]
        payload = {
            "stage": "entities",
            "percent": 75,
            "entities": entities,
            "relations_sample": [["实体A", "uses", "实体B"]],
        }
        await em.emit_and_save(
            "doc-1", 1, "entities", "Found 3 entities", payload
        )
        rows = await em.get_rows_since("doc-1", 1, after_id=0)
        assert len(rows) == 1
        row = rows[0]
        assert row["stage"] == "entities"
        assert row["message"] == "Found 3 entities"
        assert json.loads(row["payload_json"]) == payload
        # Tailing past the last row returns nothing.
        assert await em.get_rows_since("doc-1", 1, after_id=row["id"]) == []
        # Legacy rows elsewhere are not mixed in (scoped to doc + user).
        assert await em.get_rows_since("doc-1", 2, after_id=0) == []

    _run(main())


def test_event_from_row_reconstructs_and_degrades_legacy_rows():
    from app.api.progress import _event_from_row

    rich = {
        "id": 1,
        "stage": "entities",
        "message": "Found 3",
        "payload_json": json.dumps(
            {"stage": "entities", "percent": 75, "entities": ["甲"]},
            ensure_ascii=False,
        ),
        "percent": 75,
        "error_message": None,
    }
    assert _event_from_row(rich) == {
        "type": "entities",
        "message": "Found 3",
        "data": {"stage": "entities", "percent": 75, "entities": ["甲"]},
    }

    legacy = {
        "id": 2,
        "stage": "error",
        "message": "boom",
        "payload_json": None,
        "percent": 0,
        "error_message": "boom detail",
    }
    ev = _event_from_row(legacy)
    assert ev["type"] == "error"
    assert ev["data"]["stage"] == "error"
    assert ev["data"]["error"] == "boom detail"


def test_progress_event_stream_replays_then_stops_at_complete():
    from app.api.progress import _progress_event_stream

    async def main():
        await _bootstrap()
        em = ProgressEmitter()
        await em.emit_and_save(
            "doc-1", 1, "chunking", "Chunking", {"stage": "chunking", "percent": 20}
        )
        await em.emit_and_save(
            "doc-1", 1, "entities", "Found",
            {"stage": "entities", "percent": 75, "entities": ["乙"]},
        )
        await em.emit_and_save(
            "doc-1", 1, "complete", "Done", {"stage": "complete", "percent": 100}
        )
        frames = []
        async for frame in _progress_event_stream(
            em, "doc-1", 1, poll_seconds=0.01
        ):
            frames.append(frame)
        events = _parse_frames(frames)
        assert [e["type"] for e in events] == ["chunking", "entities", "complete"]
        # Rich payload survives the DB round trip verbatim.
        assert events[1]["data"]["entities"] == ["乙"]

    _run(main())


def test_progress_event_stream_stops_at_error_before_replay_exhaustion():
    from app.api.progress import _progress_event_stream

    async def main():
        await _bootstrap()
        em = ProgressEmitter()
        await em.emit_and_save(
            "doc-1", 1, "started", "Start", {"stage": "started"}
        )
        await em.emit_and_save(
            "doc-1", 1, "error", "Oops", {"stage": "error", "error": "Oops"}
        )
        frames = []
        async for frame in _progress_event_stream(
            em, "doc-1", 1, poll_seconds=0.01
        ):
            frames.append(frame)
        events = _parse_frames(frames)
        assert [e["type"] for e in events] == ["started", "error"]
        assert events[1]["data"]["error"] == "Oops"

    _run(main())


def test_progress_event_stream_tails_rows_written_mid_stream():
    from app.api.progress import _progress_event_stream

    async def main():
        await _bootstrap()
        em = ProgressEmitter()
        await em.emit_and_save(
            "doc-1", 1, "started", "Start", {"stage": "started"}
        )
        collected = []

        async def consume():
            async for frame in _progress_event_stream(
                em, "doc-1", 1, poll_seconds=0.01
            ):
                collected.append(frame)

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.05)  # let the consumer start tail-polling
        await em.emit_and_save(
            "doc-1", 1, "complete", "Done", {"stage": "complete", "percent": 100}
        )
        await asyncio.wait_for(consumer, timeout=2)
        events = _parse_frames(collected)
        assert [e["type"] for e in events] == ["started", "complete"]

    _run(main())
