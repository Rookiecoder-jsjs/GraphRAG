"""Migrate the Chroma vector index to the currently configured embedding provider.

Run when switching EMBEDDING_PROVIDER (e.g. siliconflow -> dashscope). The
providers' vector spaces are incompatible, so mixing old and new vectors in
one collection silently corrupts retrieval. This script therefore:

  1. refuses to run while the backend is serving (unless --force),
  2. records the pre-migration collection size and dimension,
  3. DELETES and recreates the ``knowledge_graph_chunks`` collection,
  4. re-embeds every chunk from SQLite (the source of truth) via the API
     using whatever provider is currently configured — the first run warms
     the new-model embedding cache; re-runs are cache-only and free,
  5. asserts every returned vector has one consistent dimension,
  6. upserts with metadata rebuilt verbatim after documents.py (L287-296)
     so downstream ``where`` filters and ``get_chunk_context`` keep working.

Idempotent (recreate-from-scratch) and crash-safe (a partial run is just
re-run). Run from backend/ so .env is picked up:

    ../.venv/Scripts/python.exe scripts/migrate_embeddings.py --dry-run
    ../.venv/Scripts/python.exe scripts/migrate_embeddings.py --yes
"""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Allow `from app...` when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import chromadb  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.services.embedding import get_embedding_service  # noqa: E402

# Must match chroma_client.py — one collection for the whole app.
COLLECTION = "knowledge_graph_chunks"
BACKEND_HEALTH_URL = "http://localhost:8001/health"


def _backend_is_running() -> bool:
    """True if something answers on the backend health endpoint."""
    try:
        with urllib.request.urlopen(BACKEND_HEALTH_URL, timeout=2) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def _load_chunks(db_path: Path) -> Tuple[List[sqlite3.Row], int]:
    """Read every chunk row. Returns (rows, blank_content_skipped_count)."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT chunk_id, document_id, user_id, content, hierarchy_path, "
        "level, prev_chunk_id, next_chunk_id FROM chunks"
    ).fetchall()
    conn.close()

    usable = [r for r in rows if (r["content"] or "").strip()]
    return usable, len(rows) - len(usable)


def _build_metadata(row: sqlite3.Row) -> Dict[str, Any]:
    """Rebuild the ingestion metadata dict (mirrors documents.py L287-296).

    hierarchy_path is stored comma-no-space in SQLite; the ingestion path
    writes comma-space into Chroma — reconstruct so vectors stay
    byte-compatible with a future re-ingest.
    """
    hp_raw = row["hierarchy_path"] or ""
    return {
        "user_id": str(row["user_id"]),
        "document_id": row["document_id"],
        "hierarchy_level": str(row["level"]) if row["level"] is not None else "",
        "hierarchy_path": ", ".join(p for p in hp_raw.split(",") if p),
        "prev_chunk_id": row["prev_chunk_id"] or "",
        "next_chunk_id": row["next_chunk_id"] or "",
    }


async def _embed_rows(rows: List[sqlite3.Row], batch_size: int) -> List[List[float]]:
    """Embed all chunk contents through the configured provider (cached)."""
    service = await get_embedding_service()
    embeddings: List[List[float]] = []
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start + batch_size]
        vectors = await service.embed_batch(
            [r["content"] for r in batch], use_cache=True
        )
        embeddings.extend(vectors)
        print(f"[info] embedded {len(embeddings)}/{len(rows)}")
    return embeddings


def _peek_collection(collection) -> Tuple[int, int | None]:
    """Current (count, dimension) of a collection without pulling all vectors."""
    count = collection.count()
    if count == 0:
        return 0, None
    sample = collection.peek(limit=1)
    vectors = sample.get("embeddings") or []
    dim = len(vectors[0]) if vectors else None
    return count, dim


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild the Chroma collection with the currently "
                    "configured EMBEDDING_PROVIDER (delete + re-embed)."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Report counts, current dimension and an estimated "
                             "cost; change nothing.")
    parser.add_argument("--batch-size", type=int, default=20,
                        help="Chunks per embed/upsert batch (default 20).")
    parser.add_argument("--yes", action="store_true",
                        help="Skip the interactive confirmation.")
    parser.add_argument("--force", action="store_true",
                        help="Run even if the backend health endpoint answers "
                             "(concurrent writes during recreate = data loss).")
    args = parser.parse_args()

    settings = get_settings()
    print(f"[info] provider={settings.EMBEDDING_PROVIDER} "
          f"model={settings.DASHSCOPE_EMBEDDING_MODEL if settings.EMBEDDING_PROVIDER == 'dashscope' else settings.EMBEDDING_MODEL}")

    db_path = Path(settings.SQLITE_PATH)
    if not db_path.is_absolute():
        db_path = Path(__file__).resolve().parent.parent / db_path
    if not db_path.exists():
        print(f"[error] SQLite DB not found at {db_path}")
        return 1

    rows, skipped_blank = _load_chunks(db_path)
    print(f"[info] SQLite: {db_path} — {len(rows)} usable chunks"
          + (f" ({skipped_blank} blank skipped)" if skipped_blank else ""))
    if not rows:
        print("[error] nothing to migrate.")
        return 1

    client = chromadb.HttpClient(host=settings.CHROMA_HOST, port=settings.CHROMA_PORT)
    try:
        existing = client.get_collection(COLLECTION)
        old_count, old_dim = _peek_collection(existing)
        print(f"[info] current collection: {old_count} vectors, dim={old_dim}")
    except Exception:
        old_count, old_dim = 0, None
        print("[info] no existing collection — fresh index")

    # Rough estimate: ~1 token per char for Chinese text at ¥0.7/M.
    total_chars = sum(len(r["content"]) for r in rows)
    est_cost = total_chars * 0.7 / 1_000_000
    print(f"[info] ~{total_chars} chars to embed, est. cost CNY {est_cost:.3f} "
          f"(cache makes re-runs free)")

    if args.dry_run:
        print("[dry-run] no changes made.")
        return 0

    if _backend_is_running() and not args.force:
        print(f"[error] backend is serving at {BACKEND_HEALTH_URL} — stop it "
              "before migrating (concurrent writes during recreate lose data), "
              "or pass --force if you know it is safe.")
        return 1

    if not args.yes:
        answer = input(f"Delete and rebuild '{COLLECTION}' "
                       f"({old_count} vectors → {len(rows)})? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("[info] aborted.")
            return 0

    started = time.time()
    print(f"[info] deleting collection '{COLLECTION}' ...")
    try:
        client.delete_collection(COLLECTION)
    except Exception as e:
        print(f"[warn] delete_collection: {e} (continuing — it may not exist)")
    collection = client.create_collection(
        name=COLLECTION, metadata={"hnsw:space": "cosine"}
    )

    embeddings = asyncio.run(_embed_rows(rows, args.batch_size))

    # One consistent dimension across the whole run — mixed dims would make
    # Chroma reject upserts and signal a provider/config mismatch.
    dims = {len(v) for v in embeddings}
    if len(dims) != 1:
        print(f"[error] inconsistent embedding dims {sorted(dims)} — aborting "
              "before upsert; collection is currently EMPTY, re-run to retry.")
        return 1
    (dim,) = dims
    print(f"[info] all vectors dim={dim}"
          + ("" if dim == settings.EMBEDDING_DIM or settings.EMBEDDING_PROVIDER != "dashscope"
             else f" (note: EMBEDDING_DIM={settings.EMBEDDING_DIM})"))

    upserted = 0
    for start in range(0, len(rows), args.batch_size):
        batch = rows[start:start + args.batch_size]
        collection.upsert(
            ids=[r["chunk_id"] for r in batch],
            documents=[r["content"] for r in batch],
            embeddings=embeddings[start:start + args.batch_size],
            metadatas=[_build_metadata(r) for r in batch],
        )
        upserted += len(batch)

    final_count = collection.count()
    elapsed = time.time() - started
    print(f"[done] upserted {upserted} chunks in {elapsed:.1f}s; "
          f"collection now holds {final_count} vectors "
          f"(was {old_count} @ dim={old_dim}, now dim={dim})")
    if final_count != upserted:
        print(f"[warn] count mismatch: upserted {upserted} but collection "
              f"reports {final_count}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
