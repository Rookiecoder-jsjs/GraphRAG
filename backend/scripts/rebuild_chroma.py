"""One-off: rebuild Chroma collection from SQLite + embedding cache.

Vectors were lost when the external Chroma container (pure in-memory) was
stopped. The chunk metadata survives in the SQLite ``chunks`` table, and
every embedding is cached in ``embedding_cache`` keyed by md5(content) +
model — so we can repopulate Chroma with ZERO LLM / embedding API calls.

Reuses ``get_chroma_client()`` so the collection name
(``knowledge_graph_chunks``), cosine distance, and upsert semantics match
the ingestion path exactly. Metadata fields mirror ``documents.py`` lines
287-296 verbatim, so downstream ``where`` filters (user_id, document_id)
and ``get_chunk_context`` (prev/next_chunk_id) keep working.

Run from backend/ (so .env is picked up by pydantic-settings):

    .venv/Scripts/python scripts/rebuild_chroma.py
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, List, Tuple

# Allow `from app...` when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.services.chroma_client import get_chroma_client  # noqa: E402

BATCH = 100


def _text_hash(text: str) -> str:
    """md5 hex of UTF-8 text — must match EmbeddingService._get_text_hash."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _load_chunks(db_path: Path, model: str) -> Tuple[List[dict], List[str]]:
    """Read every chunk row and attach its cached embedding.

    Returns (rows_with_embedding, chunk_ids_missing_cache). A chunk is
    skipped (not silently dropped) when its content is blank or no cached
    embedding exists — those need an API re-embed and are reported.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.execute(
        "SELECT chunk_id, document_id, user_id, content, hierarchy_path, "
        "level, prev_chunk_id, next_chunk_id FROM chunks"
    )
    rows = cur.fetchall()

    ready: List[dict] = []
    missing: List[str] = []
    for r in rows:
        content = r["content"] or ""
        if not content.strip():
            print(f"[warn] blank content, skipping chunk_id={r['chunk_id']}")
            continue
        cached = conn.execute(
            "SELECT embedding FROM embedding_cache "
            "WHERE text_hash = ? AND model = ?",
            (_text_hash(content), model),
        ).fetchone()
        if not cached:
            missing.append(r["chunk_id"])
            continue
        embedding: List[float] = json.loads(cached["embedding"].decode("utf-8"))

        # hierarchy_path is stored in SQLite as comma-no-space; the ingestion
        # path writes ", " (comma-space) into Chroma. Reconstruct so a future
        # re-ingest is byte-identical and idempotent.
        hp_raw = r["hierarchy_path"] or ""
        hierarchy_path = ", ".join(p for p in hp_raw.split(",") if p)
        metadata: dict[str, Any] = {
            "user_id": str(r["user_id"]),
            "document_id": r["document_id"],
            "hierarchy_level": str(r["level"]) if r["level"] is not None else "",
            "hierarchy_path": hierarchy_path,
            "prev_chunk_id": r["prev_chunk_id"] or "",
            "next_chunk_id": r["next_chunk_id"] or "",
        }
        ready.append({
            "chunk_id": r["chunk_id"],
            "content": content,
            "embedding": embedding,
            "metadata": metadata,
        })
    conn.close()
    return ready, missing


def main() -> int:
    settings = get_settings()

    db_path = Path(settings.SQLITE_PATH)
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parent.parent / db_path
    if not db_path.exists():
        print(f"[error] SQLite DB not found at {db_path}")
        return 1
    print(f"[info] SQLite: {db_path}")

    ready, missing = _load_chunks(db_path, settings.EMBEDDING_MODEL)
    print(f"[info] chunks with cached embedding: {len(ready)}")
    if missing:
        print(f"[warn] cache miss for {len(missing)} chunk(s) — API re-embed needed:")
        for cid in missing[:10]:
            print(f"         {cid}")
        if len(missing) > 10:
            print(f"         ...and {len(missing) - 10} more")

    if not ready:
        print("[error] nothing to upsert; aborting.")
        return 1

    chroma = get_chroma_client()
    total = 0
    for i in range(0, len(ready), BATCH):
        batch = ready[i:i + BATCH]
        chroma.add_chunks(
            chunk_ids=[b["chunk_id"] for b in batch],
            documents=[b["content"] for b in batch],
            embeddings=[b["embedding"] for b in batch],
            metadatas=[b["metadata"] for b in batch],
        )
        total += len(batch)
        print(f"[info] upserted batch {i // BATCH + 1}: {total}/{len(ready)}")

    # Purge stale vectors: upsert only writes/updates ids we feed it, so a
    # chunk that was deleted from SQLite OUTSIDE the API (or whose embed is
    # now missing) would linger in the collection forever. Delete every id
    # the collection holds that is not part of the ready set, so the store
    # mirrors the SQLite chunk table exactly.
    ready_ids = {b["chunk_id"] for b in ready}
    try:
        existing_ids = chroma._collection.get()["ids"]
        stale = [cid for cid in existing_ids if cid not in ready_ids]
        if stale:
            chroma._collection.delete(ids=stale)
            print(f"[info] purged {len(stale)} stale vector(s) not in SQLite")
    except Exception as e:
        print(f"[warn] stale-vector purge skipped: {e}")

    count = chroma._collection.count()
    print(f"[done] upserted {total} chunks; collection now holds {count} vectors")
    return 0


if __name__ == "__main__":
    sys.exit(main())
