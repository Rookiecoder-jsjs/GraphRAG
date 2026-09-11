"""Tests for text paste ingestion (FEAT-022): POST /api/documents/ingest-text.

Mirrors the test_url_ingest.py endpoint-test style: direct handler invocation
with a real BackgroundTasks, hermetic SQLite + UPLOAD_DIR per test. The paste
path stores the cleaned markdown as UPLOAD_DIR/{doc_id}.md so the FEAT-017
reprocess flow works — unlike URL-HTML documents whose file_path is NULL.
"""
import asyncio
import os

import pytest
from fastapi import BackgroundTasks, HTTPException
from pydantic import ValidationError

from app.database import get_db, init_db


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    """Throwaway SQLite + uploads dir per test (suite-wide pattern)."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "text_ingest_test.db"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    from app.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


async def _bootstrap():
    await init_db()
    async with get_db() as db:
        # FK is ON per connection; the documents row needs its user.
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'u1', 'x')"
        )
        await db.commit()


async def _fetch_row(doc_id):
    async with get_db() as db:
        async with db.execute(
            "SELECT status, file_path, original_filename, file_type, title "
            "FROM documents WHERE id = ?",
            (doc_id,),
        ) as cur:
            return dict(await cur.fetchone())


def test_ingest_text_creates_pending_doc_and_dispatches(tmp_path):
    from app.api.url_ingest import TextIngestRequest, ingest_text

    asyncio.run(_bootstrap())
    bt = BackgroundTasks()
    body = TextIngestRequest(
        title="粘贴笔记",
        # clean_markdown order is collapse-LF-runs THEN CRLF->LF, so CRLF
        # blank runs only get line-ending normalization (existing shared
        # util behavior — not changed in this round); pure-LF runs collapse.
        content="第一段\r\n\r\n第二段\n\n\n\n\n第二段B",
    )
    doc = asyncio.run(ingest_text(body=body, background_tasks=bt, current_user={"id": 1}))

    assert doc["status"] == "pending"
    assert doc["file_type"] == "md"
    assert doc["title"] == "粘贴笔记"
    assert len(bt.tasks) == 1
    task = bt.tasks[0]
    assert task.func.__name__ == "process_document_background"
    assert task.args[0] == doc["id"] and task.args[1] == 1
    assert task.args[2] == "第一段\n\n第二段\n\n第二段B"
    assert task.args[3] == "粘贴笔记"

    row = asyncio.run(_fetch_row(doc["id"]))
    assert row["status"] == "pending"
    assert row["file_path"]  # non-NULL: reprocess (FEAT-017) must find it
    assert row["file_type"] == "md"
    assert row["original_filename"] == "粘贴笔记.md"

    # The persisted file's bytes are exactly the pipeline input.
    with open(row["file_path"], "r", encoding="utf-8") as f:
        assert f.read() == task.args[2]


def test_ingest_text_title_from_h1(tmp_path):
    from app.api.url_ingest import TextIngestRequest, ingest_text

    asyncio.run(_bootstrap())
    doc = asyncio.run(
        ingest_text(
            body=TextIngestRequest(content="# 图谱入门\n\n正文"),
            background_tasks=BackgroundTasks(),
            current_user={"id": 1},
        )
    )
    assert doc["title"] == "图谱入门"

    # No H1 anywhere -> the fixed fallback title.
    doc2 = asyncio.run(
        ingest_text(
            body=TextIngestRequest(content="只有正文，没有标题"),
            background_tasks=BackgroundTasks(),
            current_user={"id": 1},
        )
    )
    assert doc2["title"] == "粘贴文档"


def test_ingest_text_explicit_title_wins_and_caps(tmp_path):
    from app.api.url_ingest import TextIngestRequest, ingest_text

    asyncio.run(_bootstrap())
    # Explicit title beats the H1.
    doc = asyncio.run(
        ingest_text(
            body=TextIngestRequest(title="我的标题", content="# 自动标题\n\n正文"),
            background_tasks=BackgroundTasks(),
            current_user={"id": 1},
        )
    )
    assert doc["title"] == "我的标题"

    # An unbounded H1 fallback must be truncated to 200 chars.
    huge_h1 = "# " + "长" * 500 + "\n\n正文"
    doc2 = asyncio.run(
        ingest_text(
            body=TextIngestRequest(content=huge_h1),
            background_tasks=BackgroundTasks(),
            current_user={"id": 1},
        )
    )
    assert len(doc2["title"]) == 200


def test_ingest_text_blank_rejected():
    from app.api.url_ingest import TextIngestRequest

    with pytest.raises(ValidationError):
        TextIngestRequest(content="   \n\t ")


def test_ingest_text_over_cap_422(monkeypatch):
    from app.api.url_ingest import TextIngestRequest, ingest_text

    asyncio.run(_bootstrap())
    monkeypatch.setenv("TEXT_INGEST_MAX_CHARS", "10")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        bt = BackgroundTasks()
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                ingest_text(
                    body=TextIngestRequest(content="x" * 11),
                    background_tasks=bt,
                    current_user={"id": 1},
                )
            )
        assert exc.value.status_code == 422
        assert len(bt.tasks) == 0
    finally:
        get_settings.cache_clear()
