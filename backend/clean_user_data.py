"""Clean all user-owned data from SQLite, ChromaDB, Neo4j, and the in-memory BM25 index.

Usage:
    python clean_user_data.py [user_id] [--yes] [--dry-run]

Examples:
    python clean_user_data.py              # clean user 1, with confirmation
    python clean_user_data.py 3 --yes      # clean user 3, no confirmation
    python clean_user_data.py 1 --dry-run  # show what would be deleted

CAUTION: this is destructive. The default mode requires interactive
confirmation unless --yes is passed.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

# Make the script work whether you run it as `python clean_user_data.py`
# from inside backend/ or `python backend/clean_user_data.py` from the repo root.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def _resolve_sqlite_path(raw: str) -> str:
    """Anchor a relative SQLITE_PATH to the backend directory.

    The app resolves ``SQLITE_PATH=./data/sqlite/app.db`` against its CWD
    (backend/), so running this script from the repo root used to open the
    0-byte root ``data/sqlite/app.db`` stub and report "0 tables cleaned"
    while the real DB was untouched. Mirror rebuild_chroma.py's anchoring.
    """
    path = Path(raw)
    if not path.is_absolute():
        path = (HERE / path).resolve()
    return str(path)


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Delete all data for a single user across all stores."
    )
    p.add_argument(
        "user_id",
        nargs="?",
        type=int,
        default=1,
        help="User ID to clean (default: 1).",
    )
    p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip the interactive confirmation prompt.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be deleted without touching any store.",
    )
    return p


def _confirm(user_id: int) -> bool:
    try:
        answer = input(f"Delete ALL data for user {user_id}? [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


async def clean_all_user_data(user_id: int, dry_run: bool = False) -> int:
    """Clean one user across all stores. Returns a non-zero exit code on
    failure so the CLI can abort loudly instead of reporting false success."""
    from app.config import get_settings
    from app.services.neo4j_client import get_neo4j_client
    from app.services.chroma_client import get_chroma_client
    from app.services.bm25 import get_bm25_service
    import aiosqlite

    settings = get_settings()
    sqlite_path = _resolve_sqlite_path(settings.SQLITE_PATH)
    label = "(dry-run) " if dry_run else ""
    print(f"{label}Cleaning all data for user {user_id}...")
    print("=" * 50)

    # Refuse to proceed against a database that doesn't exist yet: the real
    # DB (backend/data/sqlite/app.db) is created on first app start, so a
    # missing/empty file here means we'd be about to wipe Neo4j/Chroma/BM25
    # while reporting "SQLite cleaned (0 tables)" against the wrong path.
    if not os.path.exists(sqlite_path) or os.path.getsize(sqlite_path) == 0:
        print(f"ERROR: SQLite database missing or empty at {sqlite_path}", file=sys.stderr)
        print("  Refusing to run (stores would be left inconsistent).", file=sys.stderr)
        print("  Set SQLITE_PATH if your data lives elsewhere.", file=sys.stderr)
        return 1

    # 1. Neo4j
    print(f"\n[1/4] {label}Neo4j...")
    if not dry_run:
        neo4j = await get_neo4j_client()
        await neo4j.delete_user_data(user_id)
    print("OK: Neo4j cleaned")

    # 2. ChromaDB
    print(f"[2/4] {label}ChromaDB...")
    if not dry_run:
        chroma = get_chroma_client()
        chroma.delete_user_chunks(user_id)
    print("OK: ChromaDB cleaned")

    # 3. SQLite — clean in dependency order so FK CASCADE is consistent.
    #    `conversations` cascades to `messages` and `message_feedback`.
    #    `progress_history` has no FK on user_id so we delete it explicitly.
    #
    #    Each table is filtered against sqlite_master so the script works
    #    on databases that haven't been bootstrapped by the latest
    #    init_db() yet (e.g. running the script against a stale dev DB).
    print(f"[3/4] {label}SQLite ({sqlite_path})...")
    # `messages` has NO user_id column — it is only reachable via the
    # conversations FK (ON DELETE CASCADE), which also cascades down to
    # message_feedback. We delete them implicitly through `conversations`;
    # message_feedback is kept explicit only as belt-and-braces.
    candidate_tables = [
        "message_feedback",
        "conversations",
        "progress_history",
        "chunks",
        "documents",
    ]
    async with aiosqlite.connect(sqlite_path) as db:
        await db.execute("PRAGMA foreign_keys = ON")
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cursor:
            existing = {row[0] for row in await cursor.fetchall()}
        if not existing:
            print(f"ERROR: no tables found in {sqlite_path}", file=sys.stderr)
            print("  Refusing to report success (stores would be inconsistent).", file=sys.stderr)
            return 1
        cleaned = []
        for table in candidate_tables:
            if table not in existing:
                print(f"  skip {table} (table not present)")
                continue
            await db.execute(
                f"DELETE FROM {table} WHERE user_id = ?", (user_id,)
            )
            cleaned.append(table)
        if not dry_run:
            await db.commit()
    print(f"OK: SQLite cleaned ({len(cleaned)} tables: {', '.join(cleaned)})")

    # 4. In-memory BM25 index — must be cleared so the next upload rebuilds
    #    a fresh index from the (now empty) SQLite chunks table.
    print(f"[4/4] {label}In-memory BM25 index...")
    if not dry_run:
        get_bm25_service().clear_user(user_id)
    print("OK: BM25 index cleared")

    # 5. In-memory caches of a RUNNING server are NOT touched by this script
    #    (separate process): retrieval results and the cluster map can keep
    #    serving the wiped user's data for up to RETRIEVAL_CACHE_TTL (300s).
    #    Nothing to do here but warn — a restart clears them.
    print("\n" + "=" * 50)
    print(f"{label}DONE: All data for user {user_id} cleaned!")
    print("NOTE: if the app is running, restart it to clear its in-memory")
    print("      retrieval/cluster caches for this user.")


def main() -> int:
    args = _build_argparser().parse_args()

    if not args.dry_run and not args.yes:
        if not _confirm(args.user_id):
            print("Aborted.")
            return 1

    code = asyncio.run(clean_all_user_data(args.user_id, dry_run=args.dry_run))
    return code


if __name__ == "__main__":
    sys.exit(main())
