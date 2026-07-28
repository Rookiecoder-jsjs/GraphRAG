"""Tests for the document processing state machine (services/doc_status.py).

Covers transition rules, idempotency, failure recording, terminal guards,
retry reset, the legacy-schema migration, and the fresh-schema default.
"""
import asyncio
import os

import aiosqlite
import pytest

from app.config import get_settings
from app.database import init_db, get_db
from app.services.doc_status import (
    DocStatus,
    DocumentNotFound,
    InvalidStatusTransition,
    get_document_status,
    reset_for_retry,
    set_document_status,
    validate_transition,
)


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    """Give every test a throwaway SQLite file."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "doc_status_test.db"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def _bootstrap(user_id: int = 1) -> None:
    await init_db()
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) "
            "VALUES (?, ?, ?)",
            (user_id, f"user{user_id}", "x"),
        )
        await db.commit()


async def _insert_doc(doc_id: str, status: str = "pending", user_id: int = 1) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO documents (id, user_id, title, status) "
            "VALUES (?, ?, ?, ?)",
            (doc_id, user_id, "t", status),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Pure transition rules (no DB)
# ---------------------------------------------------------------------------
def test_validate_transition_rules():
    assert validate_transition("pending", "document_created") is True
    assert validate_transition("pending", "indexed") is True          # skip-ahead ok
    assert validate_transition("indexed", "indexed") is False         # idempotent
    assert validate_transition("graphed", "document_created") is False  # backward no-op
    assert validate_transition("indexed", "failed") is True           # may fail
    with pytest.raises(InvalidStatusTransition):
        validate_transition("ready", "failed")                        # terminal
    with pytest.raises(InvalidStatusTransition):
        validate_transition("failed", "indexed")                      # terminal


# ---------------------------------------------------------------------------
# DB-backed behaviour
# ---------------------------------------------------------------------------
def test_forward_transitions_apply():
    async def main():
        await _bootstrap()
        await _insert_doc("d1")
        assert await set_document_status("d1", DocStatus.DOCUMENT_CREATED) is True
        assert await get_document_status("d1") == "document_created"
        assert await set_document_status("d1", DocStatus.INDEXED) is True
        assert await set_document_status("d1", DocStatus.GRAPHED) is True
        assert await set_document_status("d1", DocStatus.READY) is True
        assert await get_document_status("d1") == "ready"

    asyncio.run(main())


def test_same_and_backward_are_noops():
    async def main():
        await _bootstrap()
        await _insert_doc("d2", status="indexed")
        assert await set_document_status("d2", DocStatus.INDEXED) is False
        assert await set_document_status("d2", DocStatus.DOCUMENT_CREATED) is False
        assert await get_document_status("d2") == "indexed"

    asyncio.run(main())


def test_failed_records_error_and_blocks_forward():
    async def main():
        await _bootstrap()
        await _insert_doc("d3", status="graphed")
        assert await set_document_status(
            "d3", DocStatus.FAILED, error_message="boom"
        ) is True
        async with get_db() as db:
            cur = await db.execute(
                "SELECT status, error_message FROM documents WHERE id = 'd3'"
            )
            row = await cur.fetchone()
        assert row["status"] == "failed"
        assert row["error_message"] == "boom"
        # Cannot leave 'failed' without an explicit reset.
        with pytest.raises(InvalidStatusTransition):
            await set_document_status("d3", DocStatus.READY)
        # Re-failing is an idempotent no-op.
        assert await set_document_status("d3", DocStatus.FAILED) is False

    asyncio.run(main())


def test_successful_transition_clears_error_message():
    async def main():
        await _bootstrap()
        await _insert_doc("dc", status="pending")
        # fail it, then reset + advance; error_message must be cleared.
        await set_document_status("dc", DocStatus.FAILED, error_message="x")
        await reset_for_retry("dc")
        await set_document_status("dc", DocStatus.INDEXED)
        async with get_db() as db:
            cur = await db.execute(
                "SELECT status, error_message FROM documents WHERE id = 'dc'"
            )
            row = await cur.fetchone()
        assert row["status"] == "indexed"
        assert row["error_message"] is None

    asyncio.run(main())


def test_ready_is_terminal():
    async def main():
        await _bootstrap()
        await _insert_doc("d4", status="ready")
        with pytest.raises(InvalidStatusTransition):
            await set_document_status("d4", DocStatus.FAILED)

    asyncio.run(main())


def test_reset_for_retry():
    async def main():
        await _bootstrap()
        await _insert_doc("d5", status="failed")
        assert await reset_for_retry("d5") is True
        assert await get_document_status("d5") == "pending"
        # Reset from a non-failed state is rejected.
        await _insert_doc("d6", status="indexed")
        with pytest.raises(InvalidStatusTransition):
            await reset_for_retry("d6")

    asyncio.run(main())


def test_missing_document_raises():
    async def main():
        await _bootstrap()
        with pytest.raises(DocumentNotFound):
            await set_document_status("nope", DocStatus.READY)
        assert await get_document_status("nope") is None

    asyncio.run(main())


def test_new_document_defaults_to_pending():
    async def main():
        await _bootstrap()
        async with get_db() as db:
            await db.execute(
                "INSERT INTO documents (id, user_id, title) VALUES ('dflt', 1, 't')"
            )
            await db.commit()
        assert await get_document_status("dflt") == "pending"

    asyncio.run(main())


def test_migration_backfills_legacy_rows():
    """A pre-state-machine documents table gains the columns; old rows -> ready."""

    async def main():
        settings = get_settings()
        os.makedirs(os.path.dirname(settings.SQLITE_PATH), exist_ok=True)
        # Legacy schema: no status / error_message / updated_at columns.
        async with aiosqlite.connect(settings.SQLITE_PATH) as db:
            await db.execute(
                "CREATE TABLE documents ("
                "id TEXT PRIMARY KEY, user_id INTEGER, title TEXT, "
                "file_path TEXT, original_filename TEXT, file_type TEXT, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            await db.execute(
                "INSERT INTO documents (id, user_id, title) "
                "VALUES ('legacy', 1, 'old')"
            )
            await db.commit()

        await init_db()  # should ALTER + backfill

        async with get_db() as db:
            cur = await db.execute(
                "SELECT status, error_message, updated_at "
                "FROM documents WHERE id = 'legacy'"
            )
            row = await cur.fetchone()
        assert row["status"] == "ready"
        assert row["error_message"] is None
        assert row["updated_at"] is not None

    asyncio.run(main())
