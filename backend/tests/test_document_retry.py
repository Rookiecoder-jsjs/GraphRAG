"""Tests for POST /api/documents/{doc_id}/reprocess (FEAT-017, wiring reset_for_retry).

The endpoint coroutine is exercised directly (suite-wide pattern) with a real
``BackgroundTasks`` capture. Covered: ownership 404, failed-only gating 409,
missing-file 409, conversion failure paths that must NOT touch state, and the
success path — reset to pending, error_message cleared, STALE progress_history
rows deleted (the SSE stream replays from id 0 and closes on the first
terminal frame, so a leftover error row would replay the OLD failure), and
process_document_background dispatched with (doc_id, user_id, markdown, title).
"""
import asyncio

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.documents import reprocess_document
from app.database import get_db, init_db
from app.services.doc_status import InvalidStatusTransition


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    """Throwaway SQLite per test (suite-wide pattern, see test_progress_sse)."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "retry_test.db"))
    from app.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


async def _bootstrap(
    tmp_path,
    *,
    status: str = "failed",
    create_file: bool = True,
    progress_rows: bool = True,
) -> str:
    """Create user 1 + one document in the requested state; return file_path."""
    await init_db()
    file_path = str(tmp_path / "doc-file.md")
    if create_file:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("# Hello\n\nWorld content.\n")
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) "
            "VALUES (1, 'u1', 'x')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO documents (id, user_id, title, file_path, "
            "original_filename, file_type, status, error_message) "
            "VALUES ('doc-1', 1, 'T', ?, 'a.md', 'md', ?, 'boom')",
            (file_path, status),
        )
        if progress_rows:
            # A terminal error frame from the PREVIOUS run — must be cleared
            # by reprocess so the progress SSE replays the new run instead.
            await db.execute(
                "INSERT INTO progress_history (doc_id, user_id, stage, "
                "message, is_error) VALUES ('doc-1', 1, 'error', 'old', 1)"
            )
        await db.commit()
    return file_path


def _call(user_id: int = 1, doc_id: str = "doc-1"):
    """Invoke the endpoint coroutine with a capturing BackgroundTasks."""
    bt = BackgroundTasks()
    result = asyncio.run(
        reprocess_document(doc_id=doc_id, background_tasks=bt, current_user={"id": user_id})
    )
    return result, bt


def _doc_status():
    async def main():
        async with get_db() as db:
            async with db.execute(
                "SELECT status, error_message FROM documents WHERE id = 'doc-1'"
            ) as cur:
                return dict(await cur.fetchone())

    return asyncio.run(main())


def _progress_count():
    async def main():
        async with get_db() as db:
            async with db.execute(
                "SELECT COUNT(*) AS n FROM progress_history WHERE doc_id = 'doc-1'"
            ) as cur:
                return (await cur.fetchone())["n"]

    return asyncio.run(main())


def test_reprocess_requires_failed_status(tmp_path):
    asyncio.run(_bootstrap(tmp_path, status="pending", progress_rows=False))

    def set_status(status_value: str) -> None:
        async def main():
            async with get_db() as db:
                await db.execute(
                    "UPDATE documents SET status = ? WHERE id = 'doc-1'",
                    (status_value,),
                )
                await db.commit()

        asyncio.run(main())

    for status_value in ("pending", "document_created", "indexed", "graphed", "ready"):
        set_status(status_value)
        with pytest.raises(HTTPException) as exc:
            _call()
        assert exc.value.status_code == 409
        assert _doc_status()["status"] == status_value  # untouched by the 409


def test_reprocess_404_unknown_or_foreign_doc(tmp_path):
    asyncio.run(_bootstrap(tmp_path))
    with pytest.raises(HTTPException) as exc:
        _call(user_id=2)
    assert exc.value.status_code == 404
    with pytest.raises(HTTPException) as exc:
        _call(doc_id="nope")
    assert exc.value.status_code == 404


def test_reprocess_missing_file_409_no_state_change(tmp_path):
    asyncio.run(_bootstrap(tmp_path, create_file=False))
    with pytest.raises(HTTPException) as exc:
        _call()
    assert exc.value.status_code == 409
    assert _doc_status() == {"status": "failed", "error_message": "boom"}
    assert _progress_count() == 1  # untouched


def test_reprocess_empty_conversion_422_no_reset(tmp_path, monkeypatch):
    asyncio.run(_bootstrap(tmp_path))
    monkeypatch.setattr(
        "app.api.documents.convert_document_to_markdown", lambda *a, **k: ("", None)
    )
    with pytest.raises(HTTPException) as exc:
        _call()
    assert exc.value.status_code == 422
    assert _doc_status() == {"status": "failed", "error_message": "boom"}
    assert _progress_count() == 1


def test_reprocess_conversion_exception_500_keeps_failed(tmp_path, monkeypatch):
    asyncio.run(_bootstrap(tmp_path))

    def _boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr("app.api.documents.convert_document_to_markdown", _boom)
    with pytest.raises(HTTPException) as exc:
        _call()
    assert exc.value.status_code == 500
    assert _doc_status() == {"status": "failed", "error_message": "boom"}


def test_reprocess_success_resets_clears_progress_and_dispatches(tmp_path):
    asyncio.run(_bootstrap(tmp_path))
    doc, bt = _call()

    assert doc["status"] == "pending"
    assert doc["error_message"] is None
    row = _doc_status()
    assert row == {"status": "pending", "error_message": None}
    # Stale progress rows are gone — a fresh SSE replay must not resend the
    # previous run's terminal error frame.
    assert _progress_count() == 0
    assert len(bt.tasks) == 1
    task = bt.tasks[0]
    assert task.func.__name__ == "process_document_background"
    assert task.args[0] == "doc-1" and task.args[1] == 1
    assert "World content." in task.args[2]
    assert task.args[3] == "T"


def test_reprocess_is_race_safe_on_reset(tmp_path, monkeypatch):
    asyncio.run(_bootstrap(tmp_path))

    async def _race(doc_id):
        raise InvalidStatusTransition("failed", "pending")

    monkeypatch.setattr("app.api.documents.reset_for_retry", _race)
    with pytest.raises(HTTPException) as exc:
        _call()
    assert exc.value.status_code == 409
