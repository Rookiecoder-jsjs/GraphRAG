"""Tests for the image ingestion pipeline (Phase 2a).

Covers:
  - magic-byte gate for png/jpeg uploads
  - _safe_image_path traversal/extension/missing-file guards
  - process_image_background end-to-end (success + embedding-failure
    isolation) against a real temp SQLite, with chroma/neo4j/bm25/
    progress/embedding faked at the module-attribute level.

Run: cd backend && ../.venv/Scripts/python.exe tests/test_image_pipeline.py
Exit code 0 = all passed, 1 = any failed. Also pytest-discoverable via the
sync mirror at the bottom.
"""
import asyncio
import atexit
import os
import shutil
import struct
import sys
import tempfile
import zlib

# Env MUST be set before the first get_settings() call (lru-cached).
os.environ.setdefault("JWT_SECRET", "test-only-" + "k" * 48)
os.environ.setdefault("APP_ENV", "test")

sys.path.insert(0, ".")

_TMP_ROOT = tempfile.mkdtemp(prefix="imgpipe_")
atexit.register(shutil.rmtree, _TMP_ROOT, True)
os.environ["SQLITE_PATH"] = os.path.join(_TMP_ROOT, "app.db")
os.environ["UPLOAD_DIR"] = os.path.join(_TMP_ROOT, "uploads")
os.makedirs(os.path.join(_TMP_ROOT, "uploads", "images"), exist_ok=True)

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

import app.api.documents as docs_mod  # noqa: E402
from app.database import init_db  # noqa: E402
from app.services.embedding import EmbeddingServiceError  # noqa: E402

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_failures: list = []


def check(name: str, cond: bool, detail: str = ""):
    status = PASS if cond else FAIL
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


def _png_bytes(size: int = 16) -> bytes:
    """Synthetic size×size solid-red PNG (stdlib only)."""

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload)) + tag + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    row = b"\x00" + b"\xff\x00\x00" * size
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(row * size))
        + chunk(b"IEND", b"")
    )


# ---------- fakes -------------------------------------------------------------

class FakeProgress:
    def __init__(self):
        self.events = []

    async def emit_and_save(self, doc_id, user_id, event_type, message,
                            data=None, **kwargs):
        self.events.append({"type": event_type, "data": data or {}, **kwargs})


class FakeNeo4j:
    def __init__(self):
        self.created_docs = []
        self.chunk_batches = []

    async def create_document_node(self, doc_id, user_id, title):
        self.created_docs.append((doc_id, user_id, title))

    async def create_chunk_nodes_batch(self, doc_id, user_id, chunks):
        self.chunk_batches.append((doc_id, list(chunks)))
        return len(chunks)


class FakeEmbeddingService:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    async def embed_image_bytes(self, image_bytes, media_type, use_cache=True):
        self.calls.append((len(image_bytes), media_type))
        if self.fail:
            raise EmbeddingServiceError("simulated embedding failure")
        return [0.25, 0.5, 0.75, 1.0]


class FakeChroma:
    def __init__(self):
        self.added = []

    def add_chunks(self, chunk_ids, documents, embeddings, metadatas):
        self.added.append({
            "ids": list(chunk_ids),
            "documents": list(documents),
            "embeddings": list(embeddings),
            "metadatas": list(metadatas),
        })


class FakeBM25:
    def __init__(self):
        self.indexed = []

    def add_to_index(self, user_id, contents, chunk_ids):
        self.indexed.append((user_id, list(contents), list(chunk_ids)))


def _patch_pipeline(embed_fail: bool = False):
    """Swap documents.py singletons for fakes; return (fakes, originals)."""
    fakes = {
        "progress": FakeProgress(),
        "neo4j": FakeNeo4j(),
        "embedding": FakeEmbeddingService(fail=embed_fail),
        "chroma": FakeChroma(),
        "bm25": FakeBM25(),
    }
    originals = {}

    async def _neo4j_factory():
        return fakes["neo4j"]

    async def _embedding_factory():
        return fakes["embedding"]

    for name, fn in [
        ("get_progress_emitter", lambda: fakes["progress"]),
        ("get_neo4j_client", _neo4j_factory),
        ("get_embedding_service", _embedding_factory),
        ("get_chroma_client", lambda: fakes["chroma"]),
        ("get_bm25_service", lambda: fakes["bm25"]),
    ]:
        originals[name] = getattr(docs_mod, name)
        setattr(docs_mod, name, fn)
    return fakes, originals


def _restore_pipeline(originals):
    for name, obj in originals.items():
        setattr(docs_mod, name, obj)


def _seed_image_doc(doc_id: str, user_id: int = 1, title: str = "diagram") -> str:
    """Write a real PNG under UPLOAD_DIR/images/<doc_id>/; return its path."""
    settings = get_settings()
    image_dir = os.path.join(settings.UPLOAD_DIR, "images", doc_id)
    os.makedirs(image_dir, exist_ok=True)
    path = os.path.join(image_dir, "abc123.png")
    with open(path, "wb") as f:
        f.write(_png_bytes())
    return path


# ---------- magic bytes -------------------------------------------------------

print("Upload gate: magic bytes")
check(
    "real PNG/JPEG pass; mismatched content rejected",
    docs_mod._content_matches_extension(_png_bytes(), ".png") is True
    and docs_mod._content_matches_extension(b"\xff\xd8\xff\xe0junk", ".jpg") is True
    and docs_mod._content_matches_extension(b"not a png", ".png") is False
    and docs_mod._content_matches_extension(_png_bytes(), ".jpg") is False,
)
check(
    "image extensions are in ALLOWED_EXTENSIONS",
    {".png", ".jpg", ".jpeg"} <= docs_mod.ALLOWED_EXTENSIONS,
)


# ---------- _safe_image_path --------------------------------------------------

print("\n_safe_image_path guards")
_path = _seed_image_doc("safe-doc-1")
check(
    "valid doc/filename resolves",
    docs_mod._safe_image_path("safe-doc-1", "abc123.png") is not None,
)
check(
    "traversal attempts all return None",
    docs_mod._safe_image_path("..", "abc123.png") is None
    and docs_mod._safe_image_path("safe-doc-1", "../../../etc/passwd") is None
    and docs_mod._safe_image_path("safe-doc-1", "/etc/passwd") is None,
)
check(
    "unknown extension / missing file return None",
    docs_mod._safe_image_path("safe-doc-1", "notes.txt") is None
    and docs_mod._safe_image_path("safe-doc-1", "missing.png") is None
    and docs_mod._safe_image_path("other-doc", "abc123.png") is None,
)


# ---------- process_image_background ------------------------------------------

def _sqlite_scalar(sql: str, params: tuple):
    import sqlite3
    conn = sqlite3.connect(os.environ["SQLITE_PATH"])
    try:
        row = conn.execute(sql, params).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


async def _case_image_pipeline_success():
    import sqlite3

    doc_id = "img-doc-ok"
    # User + document rows (as registration and the upload endpoint create).
    conn = sqlite3.connect(os.environ["SQLITE_PATH"])
    conn.execute(
        "INSERT OR IGNORE INTO users (id, username, password_hash) "
        "VALUES (1, 'tester', 'x')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO documents "
        "(id, user_id, title, file_path, original_filename, file_type, status) "
        "VALUES (?, 1, 'diagram', '/tmp/x', 'diagram.png', 'png', 'pending')",
        (doc_id,),
    )
    conn.commit()
    conn.close()

    stored = _seed_image_doc(doc_id)
    rel_path = f"images/{doc_id}/abc123.png"

    fakes, originals = _patch_pipeline()
    try:
        await docs_mod.process_image_background(
            doc_id, 1, stored, rel_path, "diagram"
        )
    finally:
        _restore_pipeline(originals)

    added = fakes["chroma"].added
    check(
        "chroma got one chunk: placeholder document + modality metadata",
        len(added) == 1
        and added[0]["documents"] == ["[图片: diagram]"]
        and added[0]["metadatas"][0]["modality"] == "image"
        and added[0]["metadatas"][0]["image_path"] == rel_path
        and added[0]["metadatas"][0]["user_id"] == "1"
        and added[0]["embeddings"][0] == [0.25, 0.5, 0.75, 1.0],
        f"added={added}",
    )
    check(
        "sqlite chunk row stored with modality='image' + image_path",
        _sqlite_scalar(
            "SELECT modality FROM chunks WHERE document_id = ?", (doc_id,)
        ) == "image"
        and _sqlite_scalar(
            "SELECT image_path FROM chunks WHERE document_id = ?", (doc_id,)
        ) == rel_path,
    )
    check(
        "document reached 'ready' (indexed → ready, no graph stage)",
        _sqlite_scalar(
            "SELECT status FROM documents WHERE id = ?", (doc_id,)
        ) == "ready",
    )
    complete_events = [
        e for e in fakes["progress"].events if e["type"] == "complete"
    ]
    check(
        "complete event emitted with entity_count=0",
        len(complete_events) == 1
        and complete_events[0].get("entity_count") == 0
        and complete_events[0].get("relation_count") == 0,
        f"events={[e['type'] for e in fakes['progress'].events]}",
    )
    check(
        "neo4j document node created; bm25 got the placeholder",
        fakes["neo4j"].created_docs == [(doc_id, 1, "diagram")]
        and fakes["bm25"].indexed[0][1] == ["[图片: diagram]"],
    )
    check(
        "neo4j chunk node created with placeholder content",
        len(fakes["neo4j"].chunk_batches) == 1
        and fakes["neo4j"].chunk_batches[0][1][0]["content"] == "[图片: diagram]",
        f"chunk_batches={fakes['neo4j'].chunk_batches}",
    )
    check("image file exists on disk", os.path.isfile(stored))


async def _case_image_pipeline_embed_failure_isolated():
    import sqlite3

    doc_id = "img-doc-fail"
    conn = sqlite3.connect(os.environ["SQLITE_PATH"])
    conn.execute(
        "INSERT OR IGNORE INTO users (id, username, password_hash) "
        "VALUES (1, 'tester', 'x')"
    )
    conn.execute(
        "INSERT OR REPLACE INTO documents "
        "(id, user_id, title, file_path, original_filename, file_type, status) "
        "VALUES (?, 1, 'broken', '/tmp/y', 'broken.png', 'png', 'pending')",
        (doc_id,),
    )
    conn.commit()
    conn.close()

    stored = _seed_image_doc(doc_id)
    fakes, originals = _patch_pipeline(embed_fail=True)
    try:
        await docs_mod.process_image_background(
            doc_id, 1, stored, f"images/{doc_id}/abc123.png", "broken"
        )
    finally:
        _restore_pipeline(originals)

    error_events = [e for e in fakes["progress"].events if e["type"] == "error"]
    check(
        "embedding failure → FAILED status + retryable SSE error, nothing stored",
        _sqlite_scalar(
            "SELECT status FROM documents WHERE id = ?", (doc_id,)
        ) == "failed"
        and len(error_events) == 1
        and error_events[0]["data"].get("retryable") is True
        and error_events[0]["data"].get("error_stage") == "embedding"
        and fakes["chroma"].added == [],
        f"events={[e['type'] for e in fakes['progress'].events]}",
    )


print("\nprocess_image_background")
asyncio.run(init_db())
asyncio.run(_case_image_pipeline_success())
asyncio.run(_case_image_pipeline_embed_failure_isolated())


def test_all_image_pipeline_checks_passed():
    """pytest mirror: all cases above already ran at import time."""
    assert not _failures, (
        f"{len(_failures)} image pipeline checks failed: {', '.join(_failures)}"
    )


print()
if _failures:
    print(f"\033[91m{len(_failures)} FAILED:\033[0m " + ", ".join(_failures))
    sys.exit(1)
print(f"\033[92mAll checks passed.\033[0m")
