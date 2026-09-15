"""Tests for document-level content deduplication (upload/URL/text).

Covers: content_hash determinism + sensitivity; find_duplicate_document
hit/miss/failed-exclusion; and the 409 paths on the upload and ingest-text
endpoints (same markdown → duplicate rejected, no second row, disk file
cleaned up). ingest-url shares the same gate and is exercised through the
shared helper tests plus ingest_text (same code path shape).
"""
import io
from pathlib import Path

import pytest
from fastapi import BackgroundTasks, HTTPException
from fastapi.datastructures import UploadFile

from app.api.documents import upload_document
from app.api.url_ingest import ingest_text
from app.config import get_settings
from app.database import get_db, init_db
from app.services.dedupe import content_hash, find_duplicate_document


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "dedupe_test.db"))
    # The upload path writes converted files to UPLOAD_DIR before the dedup
    # check; point it at the throwaway dir (env var wins over .env in
    # pydantic-settings, and survives get_settings.cache_clear()).
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


async def _seed_doc(user_id: int, markdown: str, status: str = "ready") -> dict:
    """Insert a document row directly (bypassing the ingest pipeline)."""
    await init_db()
    h = content_hash(markdown)
    async with get_db() as db:
        # documents.user_id is FK-constrained to users.
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) "
            "VALUES (?, 'u', 'x')",
            (user_id,),
        )
        await db.execute(
            """INSERT INTO documents
               (id, user_id, title, file_path, original_filename, file_type,
                status, content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("doc-" + h[:6], user_id, "T-" + h[:6], None, "t.txt", "txt",
             status, h),
        )
        await db.commit()
    return {"id": "doc-" + h[:6], "hash": h}


def _upload_file(name: str, content: bytes):
    return UploadFile(filename=name, file=io.BytesIO(content))


def _patch_convert(monkeypatch):
    """Make the upload path convert without any real parser."""
    monkeypatch.setattr("app.api.documents.convert_document_to_markdown",
                        lambda path, ext: ("# Same doc\nbody", "Same"))
    monkeypatch.setattr("app.api.documents.clean_markdown", lambda m: m)
    monkeypatch.setattr("app.api.documents.extract_title_from_markdown",
                        lambda m: "Same")


# =========================================================================
# content_hash
# =========================================================================


def test_content_hash_deterministic_and_sensitive():
    a = content_hash("hello world")
    assert a == content_hash("hello world")
    assert a != content_hash("hello world!")
    assert len(a) == 64  # sha256 hex


# =========================================================================
# find_duplicate_document
# =========================================================================


@pytest.mark.asyncio
async def test_find_duplicate_hits_existing_live_doc():
    seed = await _seed_doc(1, "duplicate me")
    hit = await find_duplicate_document(1, seed["hash"])
    assert hit is not None
    assert hit["id"] == seed["id"]


@pytest.mark.asyncio
async def test_find_duplicate_misses_unknown_hash_and_cross_user():
    seed = await _seed_doc(1, "duplicate me")
    assert await find_duplicate_document(1, content_hash("different")) is None
    # Different user: same content, no collision (private corpora).
    assert await find_duplicate_document(2, seed["hash"]) is None


@pytest.mark.asyncio
async def test_find_duplicate_ignores_failed_docs():
    await _seed_doc(1, "retry me", status="failed")
    # A failed row must not block re-upload after repair.
    assert await find_duplicate_document(1, content_hash("retry me")) is None


# =========================================================================
# upload_document 409
# =========================================================================


@pytest.mark.asyncio
async def test_upload_duplicate_content_409(monkeypatch):
    """Re-uploading identical markdown returns 409 and leaves no row/file."""
    await init_db()
    _patch_convert(monkeypatch)
    await _seed_doc(1, "# Same doc\nbody")

    with pytest.raises(HTTPException) as exc:
        await upload_document(
            BackgroundTasks(), _upload_file("dup.txt", b"ignored"), {"id": 1}
        )
    assert exc.value.status_code == 409
    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE user_id = 1"
        ) as cur:
            row = await cur.fetchone()
    assert row["n"] == 1  # only the seeded row — no second insert
    # The converted file written before the dedup check must not linger.
    upload_dir = Path(get_settings().UPLOAD_DIR)
    leftover = list(upload_dir.iterdir()) if upload_dir.exists() else []
    assert leftover == [], f"stray files: {leftover}"


@pytest.mark.asyncio
async def test_upload_distinct_content_passes_dedup(monkeypatch):
    """Different content must NOT 409 — it proceeds (and fails later on the
    real stores, which is fine: not a duplicate)."""
    await init_db()
    _patch_convert(monkeypatch)
    monkeypatch.setattr("app.api.documents.convert_document_to_markdown",
                        lambda path, ext: ("# Other doc\nbody", "Other"))
    await _seed_doc(1, "# Same doc\nbody")

    try:
        await upload_document(
            BackgroundTasks(), _upload_file("other.txt", b"x"), {"id": 1}
        )
    except HTTPException as e:
        assert e.status_code != 409, "distinct content must not collide"


# =========================================================================
# ingest_text 409
# =========================================================================


@pytest.mark.asyncio
async def test_ingest_text_duplicate_409(monkeypatch):
    await init_db()
    monkeypatch.setattr("app.api.url_ingest.clean_markdown", lambda m: m)
    monkeypatch.setattr("app.api.url_ingest.extract_title_from_markdown",
                        lambda m: None)
    await _seed_doc(1, "pasted content")

    from app.api.url_ingest import TextIngestRequest

    with pytest.raises(HTTPException) as exc:
        await ingest_text(
            TextIngestRequest(content="pasted content"),
            BackgroundTasks(),
            {"id": 1},
        )
    assert exc.value.status_code == 409
    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE user_id = 1"
        ) as cur:
            row = await cur.fetchone()
    assert row["n"] == 1


@pytest.mark.asyncio
async def test_ingest_text_distinct_content_ok(monkeypatch):
    await init_db()
    monkeypatch.setattr("app.api.url_ingest.clean_markdown", lambda m: m)
    monkeypatch.setattr("app.api.url_ingest.extract_title_from_markdown",
                        lambda m: None)
    await _seed_doc(1, "existing content")

    from app.api.url_ingest import TextIngestRequest

    try:
        await ingest_text(
            TextIngestRequest(content="brand new text"),
            BackgroundTasks(),
            {"id": 1},
        )
    except HTTPException as e:
        assert e.status_code != 409, "distinct content must not collide"
