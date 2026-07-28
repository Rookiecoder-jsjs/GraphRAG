"""SQLite database connection and initialization."""
import os
import aiosqlite
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from app.config import get_settings


async def _ensure_document_status_columns(db) -> None:
    """Idempotently add the state-machine columns to an existing documents table.

    ``CREATE TABLE IF NOT EXISTS`` never alters a table that already exists, so
    databases created before the processing state machine was introduced lack
    these columns. We add them and backfill legacy rows as ``ready`` — those
    documents were processed under the old system and already live in the
    stores, so they must not surface as stuck-in-``pending``.
    """
    async with db.execute("PRAGMA table_info(documents)") as cursor:
        existing = {row[1] for row in await cursor.fetchall()}

    if "status" not in existing:
        await db.execute("ALTER TABLE documents ADD COLUMN status TEXT")
        await db.execute(
            "UPDATE documents SET status = 'ready' WHERE status IS NULL"
        )
    if "error_message" not in existing:
        await db.execute("ALTER TABLE documents ADD COLUMN error_message TEXT")
    if "updated_at" not in existing:
        await db.execute("ALTER TABLE documents ADD COLUMN updated_at TIMESTAMP")
        await db.execute(
            "UPDATE documents SET updated_at = created_at WHERE updated_at IS NULL"
        )


async def init_db():
    """Initialize SQLite database with required tables."""
    settings = get_settings()

    # Ensure directory exists
    os.makedirs(os.path.dirname(settings.SQLITE_PATH), exist_ok=True)

    async with aiosqlite.connect(settings.SQLITE_PATH) as db:
        # Enforce foreign keys (SQLite keeps them OFF by default) so the
        # ON DELETE CASCADE rules declared below actually take effect.
        await db.execute("PRAGMA foreign_keys = ON")
        # Create users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create documents table.
        # `status` is the processing state machine (see services/doc_status.py):
        # pending -> document_created -> indexed -> graphed -> ready, with
        # `failed` reachable from any non-terminal state. Defaults to 'pending'
        # so a row that never gets processed is visibly unprocessed rather than
        # silently treated as done.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT,
                file_path TEXT,
                original_filename TEXT,
                file_type TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                error_message TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        # Migrate databases created before the state machine existed.
        await _ensure_document_status_columns(db)

        # Create chunks table for tracking
        await db.execute("""
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                content TEXT,
                hierarchy_path TEXT,
                level INTEGER,
                prev_chunk_id TEXT,
                next_chunk_id TEXT,
                chroma_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Create embedding cache table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS embedding_cache (
                text_hash TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                embedding BLOB NOT NULL,
                model TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create conversations table for chat history
        await db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                title TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            )
        """)

        # Create progress history table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS progress_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                stage TEXT NOT NULL,
                message TEXT,
                percent INTEGER DEFAULT 0,
                is_complete INTEGER DEFAULT 0,
                is_error INTEGER DEFAULT 0,
                error_message TEXT,
                entity_count INTEGER DEFAULT 0,
                relation_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create message feedback table (👍 / 👎 on assistant messages)
        # One row per (message_id, user_id) — replacing/updating the rating
        # is treated as "set the current opinion" rather than appending.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS message_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                conversation_id TEXT NOT NULL,
                rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (message_id, user_id),
                FOREIGN KEY (message_id) REFERENCES messages(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)

        # Per-document user-defined tags. One tag per (document, user) — adding
        # the same tag twice is a no-op rather than a duplicate row. Cascade
        # delete on document/user removal keeps the table tidy.
        await db.execute("""
            CREATE TABLE IF NOT EXISTS document_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                tag TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (document_id, user_id, tag),
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)
        # Indexes for the two query shapes we actually run:
        #   * list tags for one doc   -> (document_id, user_id)
        #   * user-wide tag rollup    -> (user_id, tag)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_document_tags_doc
                ON document_tags (document_id, user_id)
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_document_tags_user_tag
                ON document_tags (user_id, tag)
        """)

        await db.commit()


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Get database connection as async context manager."""
    settings = get_settings()
    async with aiosqlite.connect(settings.SQLITE_PATH) as db:
        db.row_factory = aiosqlite.Row
        # PRAGMA is per-connection; enable FK enforcement on every connection
        # so cascade deletes (messages, tags, feedback) work app-wide.
        await db.execute("PRAGMA foreign_keys = ON")
        yield db
