"""Progress tracking using Server-Sent Events (SSE)."""
import asyncio
import logging
from typing import Dict, Optional, List, Set
from collections import defaultdict
import aiosqlite

from app.config import get_settings

logger = logging.getLogger(__name__)


class ProgressEmitter:
    """Centralized progress event emitter using asyncio."""

    def __init__(self):
        # Each subscriber gets its OWN queue. A single shared queue per doc_id
        # made multiple watchers (e.g. two browser tabs) steal events
        # round-robin, and any one client disconnecting deleted the queue out
        # from under the others. A set of per-subscriber queues fixes both.
        self._subscribers: Dict[str, Set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, doc_id: str) -> asyncio.Queue:
        """Create and register a fresh per-subscriber queue for a document."""
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers[doc_id].add(queue)
        return queue

    def unsubscribe(self, doc_id: str, queue: Optional[asyncio.Queue] = None):
        """Remove a subscriber's queue.

        When ``queue`` is given, only that subscriber is removed (preferred).
        Omitting it drops every subscriber for the doc — kept for backwards
        compatibility, but avoid it when multiple clients may be watching.
        """
        if queue is None:
            self._subscribers.pop(doc_id, None)
            return
        queues = self._subscribers.get(doc_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                self._subscribers.pop(doc_id, None)

    async def emit(self, doc_id: str, progress_type: str, message: str, data: dict = None):
        """Emit a progress event to every subscriber of a document."""
        event = {
            "type": progress_type,
            "message": message,
            "data": data or {}
        }

        # Iterate a snapshot so a concurrent unsubscribe can't mutate the set.
        for queue in list(self._subscribers.get(doc_id, ())):
            await queue.put(event)

    async def emit_and_save(self, doc_id: str, user_id: int, progress_type: str, message: str,
                           data: dict = None, entity_count: int = 0, relation_count: int = 0):
        """Emit a progress event and save to history."""
        # Emit to SSE
        await self.emit(doc_id, progress_type, message, data)

        # Save to database
        percent = data.get("percent", 0) if data else 0
        is_complete = 1 if progress_type == "complete" else 0
        is_error = 1 if progress_type == "error" else 0
        error_message = data.get("error") if progress_type == "error" else None

        await self.save_progress(
            doc_id, user_id, progress_type, message, percent,
            is_complete, is_error, error_message, entity_count, relation_count
        )

    async def save_progress(self, doc_id: str, user_id: int, stage: str, message: str,
                          percent: int = 0, is_complete: int = 0, is_error: int = 0,
                          error_message: str = None, entity_count: int = 0, relation_count: int = 0):
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
                    (doc_id, user_id, stage, message, percent, is_complete, is_error, error_message, entity_count, relation_count)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (doc_id, user_id, stage, message, percent, is_complete, is_error, error_message, entity_count, relation_count))
                await db.commit()
        except Exception as e:
            logger.warning("Failed to save progress: %s", e, exc_info=True)

    async def get_history(self, doc_id: str, user_id: int) -> List[Dict]:
        """Get progress history for a document."""
        settings = get_settings()
        logger.info("Getting history for doc_id=%s, user_id=%d", doc_id, user_id)
        try:
            async with aiosqlite.connect(settings.SQLITE_PATH) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("""
                    SELECT stage, message, percent, is_complete, is_error, error_message, entity_count, relation_count, created_at
                    FROM progress_history
                    WHERE doc_id = ? AND user_id = ?
                    ORDER BY id ASC
                """, (doc_id, user_id)) as cursor:
                    rows = await cursor.fetchall()
                    result = [dict(row) for row in rows]
                    logger.info("Found %d history events", len(result))
                    return result
        except Exception as e:
            logger.warning("Failed to get history: %s", e, exc_info=True)
            return []


# Singleton instance
_progress_emitter: Optional[ProgressEmitter] = None


def get_progress_emitter() -> ProgressEmitter:
    """Get the singleton progress emitter instance."""
    global _progress_emitter
    if _progress_emitter is None:
        _progress_emitter = ProgressEmitter()
    return _progress_emitter
