"""Progress tracking using Server-Sent Events (SSE)."""
import asyncio
import json
from typing import Dict, Callable, Optional, List
from collections import defaultdict
import aiosqlite

from app.config import get_settings


class ProgressEmitter:
    """Centralized progress event emitter using asyncio."""

    def __init__(self):
        self._subscribers: Dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self._locks: Dict[str, asyncio.Lock] = {}

    def subscribe(self, doc_id: str) -> asyncio.Queue:
        """Subscribe to progress updates for a document."""
        if doc_id not in self._locks:
            self._locks[doc_id] = asyncio.Lock()
        return self._subscribers[doc_id]

    def unsubscribe(self, doc_id: str):
        """Unsubscribe from progress updates."""
        if doc_id in self._subscribers:
            del self._subscribers[doc_id]
        if doc_id in self._locks:
            del self._locks[doc_id]

    async def emit(self, doc_id: str, progress_type: str, message: str, data: dict = None):
        """Emit a progress event."""
        event = {
            "type": progress_type,
            "message": message,
            "data": data or {}
        }

        if doc_id in self._subscribers:
            await self._subscribers[doc_id].put(event)

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

    async def emit_chunking(self, doc_id: str, current: int, total: int):
        """Emit chunking progress."""
        await self.emit(doc_id, "chunking", f"Chunking document: {current}/{total}", {
            "stage": "chunking",
            "current": current,
            "total": total,
            "percent": int(current / total * 100) if total > 0 else 0
        })

    async def emit_embedding(self, doc_id: str, current: int, total: int):
        """Emit embedding progress."""
        await self.emit(doc_id, "embedding", f"Creating embeddings: {current}/{total}", {
            "stage": "embedding",
            "current": current,
            "total": total,
            "percent": int(current / total * 100) if total > 0 else 0
        })

    async def emit_entity_extraction(self, doc_id: str, current: int, total: int, stage: str = "extracting"):
        """Emit entity extraction progress."""
        stage_messages = {
            "extracting": "Extracting entities",
            "llm": "Running LLM extraction",
            "relations": "Extracting relations",
            "complete": "Entity extraction complete"
        }
        message = stage_messages.get(stage, f"Processing: {current}/{total}")
        await self.emit(doc_id, "entity_extraction", message, {
            "stage": stage,
            "current": current,
            "total": total,
            "percent": int(current / total * 100) if total > 0 else 0
        })

    async def emit_complete(self, doc_id: str, entity_count: int = 0, relation_count: int = 0):
        """Emit completion event."""
        await self.emit(doc_id, "complete", "Document processing complete", {
            "stage": "complete",
            "entity_count": entity_count,
            "relation_count": relation_count,
            "percent": 100
        })

    async def emit_error(self, doc_id: str, error: str):
        """Emit error event."""
        await self.emit(doc_id, "error", f"Error: {error}", {
            "stage": "error",
            "error": error
        })

    async def save_progress(self, doc_id: str, user_id: int, stage: str, message: str,
                          percent: int = 0, is_complete: int = 0, is_error: int = 0,
                          error_message: str = None, entity_count: int = 0, relation_count: int = 0):
        """Save progress to database."""
        settings = get_settings()
        try:
            async with aiosqlite.connect(settings.SQLITE_PATH) as db:
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

    async def clear_history(self, doc_id: str, user_id: int):
        """Clear progress history for a document."""
        settings = get_settings()
        try:
            async with aiosqlite.connect(settings.SQLITE_PATH) as db:
                await db.execute("DELETE FROM progress_history WHERE doc_id = ? AND user_id = ?", (doc_id, user_id))
                await db.commit()
        except Exception as e:
            logger.warning("Failed to clear history: %s", e, exc_info=True)


# Singleton instance
_progress_emitter: Optional[ProgressEmitter] = None


def get_progress_emitter() -> ProgressEmitter:
    """Get the singleton progress emitter instance."""
    global _progress_emitter
    if _progress_emitter is None:
        _progress_emitter = ProgressEmitter()
    return _progress_emitter
