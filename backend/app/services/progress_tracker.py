"""Progress tracking for document processing (SSE via SQLite polling).

Progress events are *persisted* to the ``progress_history`` table and read
back by the SSE endpoint via polling — there is deliberately NO in-process
event bus. That is what makes the progress stream worker-agnostic (the upload
pipeline and the SSE connection may land on different uvicorn workers) and
reconnect-safe (a fresh connection replays what already happened, then tails
new rows). The rich per-event payload (stage / percent / entities /
relations_sample / ...) is stored as JSON in ``payload_json`` so the live
stream reproduces exactly what the old in-memory feed carried.
"""
import json
import logging
from typing import Dict, List, Optional

import aiosqlite

from app.config import get_settings

logger = logging.getLogger(__name__)


class ProgressEmitter:
    """Writes progress events to SQLite; reads them back for SSE / history."""

    async def emit_and_save(self, doc_id: str, user_id: int, progress_type: str,
                            message: str, data: dict = None, entity_count: int = 0,
                            relation_count: int = 0):
        """Persist a progress event together with its rich ``data`` payload."""
        percent = data.get("percent", 0) if data else 0
        is_complete = 1 if progress_type == "complete" else 0
        is_error = 1 if progress_type == "error" else 0
        error_message = data.get("error") if progress_type == "error" else None
        # Persist the FULL payload so the SSE stream can replay it verbatim
        # (the in-memory queue that used to carry it is gone).
        payload_json = json.dumps(data, ensure_ascii=False) if data else None

        await self.save_progress(
            doc_id, user_id, progress_type, message, percent,
            is_complete, is_error, error_message, entity_count, relation_count,
            payload_json,
        )

    async def save_progress(self, doc_id: str, user_id: int, stage: str, message: str,
                            percent: int = 0, is_complete: int = 0, is_error: int = 0,
                            error_message: str = None, entity_count: int = 0,
                            relation_count: int = 0, payload_json: str = None):
        """Save progress to database."""
        settings = get_settings()
        try:
            async with aiosqlite.connect(settings.SQLITE_PATH) as db:
                # This write races chat/upload writers on the same SQLite
                # file; without a busy_timeout a contended INSERT fails
                # instantly with "database is locked" and the progress row
                # is silently dropped (the SSE side still works, but the
                # history panel shows gaps).
                await db.execute("PRAGMA busy_timeout = 5000")
                await db.execute("""
                    INSERT INTO progress_history
                    (doc_id, user_id, stage, message, percent, is_complete,
                     is_error, error_message, entity_count, relation_count,
                     payload_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (doc_id, user_id, stage, message, percent, is_complete,
                      is_error, error_message, entity_count, relation_count,
                      payload_json))
                await db.commit()
        except Exception as e:
            logger.warning("Failed to save progress: %s", e, exc_info=True)

    async def get_history(self, doc_id: str, user_id: int) -> List[Dict]:
        """Get progress history for a document (fixed column shape for the API)."""
        settings = get_settings()
        try:
            async with aiosqlite.connect(settings.SQLITE_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("""
                    SELECT stage, message, percent, is_complete, is_error,
                           error_message, entity_count, relation_count, created_at
                    FROM progress_history
                    WHERE doc_id = ? AND user_id = ?
                    ORDER BY id ASC
                """, (doc_id, user_id)) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        except Exception as e:
            logger.warning("Failed to get history: %s", e, exc_info=True)
            return []

    async def get_rows_since(self, doc_id: str, user_id: int,
                             after_id: int = 0, limit: int = 500) -> List[Dict]:
        """Return rows newer than ``after_id``, ascending.

        The SSE endpoint calls this once with ``after_id=0`` to replay the
        document's whole lifecycle, then tails with the last-seen id. Includes
        ``id`` and ``payload_json`` so callers can page and reconstruct events.
        """
        settings = get_settings()
        async with aiosqlite.connect(settings.SQLITE_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT id, stage, message, percent, is_complete, is_error,
                       error_message, entity_count, relation_count, payload_json
                FROM progress_history
                WHERE doc_id = ? AND user_id = ? AND id > ?
                ORDER BY id ASC LIMIT ?
            """, (doc_id, user_id, after_id, limit)) as cursor:
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]


# Singleton instance
_progress_emitter: Optional[ProgressEmitter] = None


def get_progress_emitter() -> ProgressEmitter:
    """Get the singleton progress emitter instance."""
    global _progress_emitter
    if _progress_emitter is None:
        _progress_emitter = ProgressEmitter()
    return _progress_emitter
