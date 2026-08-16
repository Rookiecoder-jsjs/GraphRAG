"""Tests for the document upload/chunks/delete pipeline (api/documents.py).

Covers the fixes shipped to the document processing pipeline:
  1. /chunks read-side hierarchy_path parsing normalises BOTH historical
     separators (", " from legacy Chroma metadata and "," from SQLite).
  2. delete removes progress_history rows in the same transaction as the
     document row (no FK -> would otherwise orphan).
  3. delete removes the on-disk file LAST, after every store is purged, and a
     file-removal failure is logged (warning) rather than failing the delete.
  4. upload rejects a missing filename with 400 instead of 500.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import unittest.mock as _mock

import pytest
from fastapi import UploadFile, HTTPException


# =========================================================================
# Shared fakes
# =========================================================================

class _FakeCursor:
    """Async-context-manager cursor returning a fixed scripted row set."""

    def __init__(self, rows):
        self._rows = list(rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetchall(self):
        return list(self._rows)

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeExec:
    """Mirror of aiosqlite's Connection.execute() return value.

    The endpoint uses BOTH ``async with db.execute(...)`` (SELECTs) and
    ``await db.execute(...)`` (DELETEs), so the fake must be an async context
    manager AND awaitable at the same time, like the real _AsyncGenerator-
    ContextManager returned by aiosqlite.
    """

    def __init__(self, cursor):
        self._cursor = cursor

    def __await__(self):
        async def _ready():
            return self._cursor
        return _ready().__await__()

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *a):
        return False


class _DeleteDb:
    """SQLite stand-in for the delete endpoint.

    Dispatches SELECTs by their SQL prefix (file_path -> doc row, chunk_id ->
    chunk rows) and records every statement so tests can assert on ordering
    and parameters (especially the progress_history DELETE).
    """

    def __init__(self, doc_row, chunk_rows):
        self._doc_row = doc_row
        self._chunk_rows = chunk_rows
        self.statements: list = []

    def execute(self, sql, params=()):
        s = str(sql).strip()
        self.statements.append((s, tuple(params)))
        upper = s.upper()
        if upper.startswith("SELECT FILE_PATH"):
            return _FakeExec(_FakeCursor([self._doc_row] if self._doc_row else []))
        if upper.startswith("SELECT CHUNK_ID"):
            return _FakeExec(_FakeCursor(list(self._chunk_rows)))
        return _FakeExec(_FakeCursor([]))

    async def commit(self):
        pass


class _DeleteCtx:
    """Context manager yielding the shared fake DB (get_db is entered twice)."""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


class _ChunksDb:
    """SQLite stand-in for the /chunks endpoint (chunks read + doc-exists check)."""

    def __init__(self, chunk_rows, doc_row=None):
        self._chunk_rows = chunk_rows
        self._doc_row = doc_row

    def execute(self, sql, params=()):
        if "from chunks" in str(sql).lower():
            return _FakeExec(_FakeCursor(list(self._chunk_rows)))
        return _FakeExec(_FakeCursor([self._doc_row] if self._doc_row else []))


class _Ctx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *a):
        return False


# =========================================================================
# 1. _split_hierarchy_path — both historical separators
# =========================================================================

def test_split_hierarchy_path_handles_both_separators():
    from app.api.documents import _split_hierarchy_path

    # SQLite write format: ",".join(path)
    assert _split_hierarchy_path("Intro,Section,Subsection") == \
        ["Intro", "Section", "Subsection"]
    # Legacy Chroma metadata format: ", ".join(path)
    assert _split_hierarchy_path("Intro, Section, Subsection") == \
        ["Intro", "Section", "Subsection"]
    # Single-segment path with no comma at all.
    assert _split_hierarchy_path("Document") == ["Document"]
    # The retriever's "[Part N]" suffix survives splitting unchanged.
    assert _split_hierarchy_path("Intro,Section,[Part 2]") == \
        ["Intro", "Section", "[Part 2]"]
    # Empty / blank input -> empty list (never a [""] segment).
    assert _split_hierarchy_path("") == []
    assert _split_hierarchy_path("   ") == []


# =========================================================================
# 2. /chunks endpoint — read-side path parsing
# =========================================================================

def test_chunks_endpoint_parses_both_path_formats():
    from app.api import documents as docs_mod
    from app.main import app
    from app.api import auth as auth_mod

    rows = [
        {"chunk_id": "c1", "content": "x", "hierarchy_path": "Intro, Section A",
         "level": 1, "prev_chunk_id": None, "next_chunk_id": None,
         "created_at": "2026-01-01"},
        {"chunk_id": "c2", "content": "y", "hierarchy_path": "Intro,Section B",
         "level": 1, "prev_chunk_id": "c1", "next_chunk_id": None,
         "created_at": "2026-01-01"},
    ]
    ctx = _Ctx(_ChunksDb(chunk_rows=rows))
    with _mock.patch.object(docs_mod, "get_db", lambda: ctx):
        app.dependency_overrides[auth_mod.get_current_user] = lambda: {"id": 1}
        try:
            from fastapi.testclient import TestClient
            client = TestClient(app)
            r = client.get("/api/documents/d-1/chunks")
        finally:
            app.dependency_overrides.pop(auth_mod.get_current_user, None)

    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body, list) and len(body) == 2
    # ", " legacy form is split into segments, not left as one element.
    assert body[0]["hierarchy"]["path"] == ["Intro", "Section A"]
    # "," SQLite form is split too.
    assert body[1]["hierarchy"]["path"] == ["Intro", "Section B"]
    assert body[0]["hierarchy"]["level"] == 1


def test_chunks_endpoint_empty_but_doc_exists_returns_empty_list():
    from app.api import documents as docs_mod
    from app.main import app
    from app.api import auth as auth_mod

    # Doc exists (second query), but chunking may still be running.
    ctx = _Ctx(_ChunksDb(chunk_rows=[], doc_row={"id": "d-1"}))
    with _mock.patch.object(docs_mod, "get_db", lambda: ctx):
        app.dependency_overrides[auth_mod.get_current_user] = lambda: {"id": 1}
        try:
            from fastapi.testclient import TestClient
            client = TestClient(app)
            r = client.get("/api/documents/d-1/chunks")
        finally:
            app.dependency_overrides.pop(auth_mod.get_current_user, None)

    assert r.status_code == 200
    assert r.json() == []


def test_chunks_endpoint_404_for_missing_doc():
    from app.api import documents as docs_mod
    from app.main import app
    from app.api import auth as auth_mod

    # No chunks and no doc -> 404, not an empty list.
    ctx = _Ctx(_ChunksDb(chunk_rows=[], doc_row=None))
    with _mock.patch.object(docs_mod, "get_db", lambda: ctx):
        app.dependency_overrides[auth_mod.get_current_user] = lambda: {"id": 1}
        try:
            from fastapi.testclient import TestClient
            client = TestClient(app)
            r = client.get("/api/documents/nope/chunks")
        finally:
            app.dependency_overrides.pop(auth_mod.get_current_user, None)

    assert r.status_code == 404


# =========================================================================
# 3. delete — progress_history cleanup + file-removed-last ordering
# =========================================================================

def test_delete_cleans_progress_history_and_removes_file_last(tmp_path):
    from app.api import documents as docs_mod
    from app.main import app
    from app.api import auth as auth_mod

    data_file = tmp_path / "data.bin"
    data_file.write_bytes(b"payload")

    db = _DeleteDb(
        doc_row={"file_path": str(data_file)},
        chunk_rows=[{"chunk_id": "c1"}, {"chunk_id": "c2"}],
    )
    ctx = _DeleteCtx(db)

    chroma = _mock.Mock()
    neo4j = _mock.AsyncMock()
    bm25 = _mock.Mock()
    calls: list = []
    chroma.delete_document_chunks.side_effect = lambda *a, **k: calls.append("chroma")

    async def _neo_del(*a, **k):
        calls.append("neo4j")

    neo4j.delete_document.side_effect = _neo_del
    bm25.remove_from_index.side_effect = lambda *a, **k: calls.append("bm25")

    real_remove = os.remove

    def _file_del(path):
        calls.append("file")
        real_remove(path)

    with _mock.patch.object(docs_mod, "get_db", lambda: ctx), \
         _mock.patch.object(docs_mod, "get_chroma_client", lambda: chroma), \
         _mock.patch.object(docs_mod, "get_neo4j_client",
                            _mock.AsyncMock(return_value=neo4j)), \
         _mock.patch.object(docs_mod, "get_bm25_service", lambda: bm25), \
         _mock.patch.object(docs_mod, "invalidate_retrieval_cache"), \
         _mock.patch.object(docs_mod.os, "remove", side_effect=_file_del):
        app.dependency_overrides[auth_mod.get_current_user] = lambda: {"id": 7}
        try:
            from fastapi.testclient import TestClient
            client = TestClient(app)
            r = client.delete("/api/documents/d-1")
        finally:
            app.dependency_overrides.pop(auth_mod.get_current_user, None)

    assert r.status_code == 200, r.text
    # progress_history DELETE was issued with the doc + user scoping.
    ph_deletes = [st for st in db.statements if "DELETE FROM progress_history" in st[0]]
    assert len(ph_deletes) == 1
    assert ph_deletes[0][1] == ("d-1", 7)
    # chunks + documents deletes also issued in the same connection.
    assert any("DELETE FROM chunks" in st[0] for st in db.statements)
    assert any("DELETE FROM documents" in st[0] for st in db.statements)
    # Deletion order: every store first, the on-disk file LAST.
    assert calls == ["chroma", "neo4j", "bm25", "file"]
    # The file really was removed.
    assert not data_file.exists()


def test_delete_404_for_missing_doc_does_not_touch_stores():
    from app.api import documents as docs_mod
    from app.main import app
    from app.api import auth as auth_mod

    db = _DeleteDb(doc_row=None, chunk_rows=[])
    ctx = _DeleteCtx(db)
    chroma = _mock.Mock()
    neo4j = _mock.AsyncMock()
    bm25 = _mock.Mock()

    with _mock.patch.object(docs_mod, "get_db", lambda: ctx), \
         _mock.patch.object(docs_mod, "get_chroma_client", lambda: chroma), \
         _mock.patch.object(docs_mod, "get_neo4j_client",
                            _mock.AsyncMock(return_value=neo4j)), \
         _mock.patch.object(docs_mod, "get_bm25_service", lambda: bm25), \
         _mock.patch.object(docs_mod, "invalidate_retrieval_cache"):
        app.dependency_overrides[auth_mod.get_current_user] = lambda: {"id": 1}
        try:
            from fastapi.testclient import TestClient
            client = TestClient(app)
            r = client.delete("/api/documents/nope")
        finally:
            app.dependency_overrides.pop(auth_mod.get_current_user, None)

    assert r.status_code == 404
    chroma.delete_document_chunks.assert_not_called()
    neo4j.delete_document.assert_not_called()
    bm25.remove_from_index.assert_not_called()


def test_delete_file_removal_failure_is_warning_not_error(tmp_path, caplog):
    from app.api import documents as docs_mod
    from app.main import app
    from app.api import auth as auth_mod

    data_file = tmp_path / "locked.bin"
    data_file.write_bytes(b"payload")

    db = _DeleteDb(
        doc_row={"file_path": str(data_file)},
        chunk_rows=[{"chunk_id": "c1"}],
    )
    ctx = _DeleteCtx(db)

    with _mock.patch.object(docs_mod, "get_db", lambda: ctx), \
         _mock.patch.object(docs_mod, "get_chroma_client", _mock.Mock()), \
         _mock.patch.object(docs_mod, "get_neo4j_client",
                            _mock.AsyncMock(return_value=_mock.AsyncMock())), \
         _mock.patch.object(docs_mod, "get_bm25_service", _mock.Mock()), \
         _mock.patch.object(docs_mod, "invalidate_retrieval_cache"), \
         _mock.patch.object(docs_mod.os, "remove",
                            side_effect=OSError("permission denied")):
        app.dependency_overrides[auth_mod.get_current_user] = lambda: {"id": 1}
        try:
            from fastapi.testclient import TestClient
            with caplog.at_level(logging.WARNING, logger="app.api.documents"):
                client = TestClient(app)
                r = client.delete("/api/documents/d-1")
        finally:
            app.dependency_overrides.pop(auth_mod.get_current_user, None)

    # Orphaned file must not fail the delete response.
    assert r.status_code == 200
    assert any("Failed to delete file" in rec.getMessage()
               for rec in caplog.records)


# =========================================================================
# 4. upload — missing filename -> 400 (not 500)
# =========================================================================

def test_upload_rejects_missing_filename():
    from app.api.documents import upload_document

    f = UploadFile(filename=None, file=io.BytesIO(b"# hello"))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(upload_document(None, f, {"id": 1}))
    assert exc.value.status_code == 400
