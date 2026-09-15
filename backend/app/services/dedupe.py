"""Document-level content deduplication.

Uploads / URL ingests / pasted text hash the FINAL markdown (post-conversion,
post-clean) so the same content arriving through different sources collides.
A sha256 of the markdown text is content-based and format-agnostic — two
files that convert to identical markdown are the same document for the
knowledge base.

Scope: per-user. The same document ingested by two different users is
intentional (private corpora); only repeat ingestions by ONE user are treated
as duplicates.

Failed documents are deliberately excluded from the duplicate check: a doc
that failed processing is usually re-uploaded after fixing the file, and the
earlier attempt's row would otherwise block it forever.
"""
import hashlib
from typing import Optional

from app.database import get_db


def content_hash(markdown: str) -> str:
    """sha256 of the final markdown text (utf-8)."""
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


async def find_duplicate_document(user_id: int, markdown_hash: str) -> Optional[dict]:
    """Return an existing live document of ``user_id`` with this content hash.

    ``None`` when no duplicate exists (safe to proceed). ``status != 'failed'``
    excludes dead rows so a failed ingest can be retried after repair.
    """
    async with get_db() as db:
        async with db.execute(
            """SELECT id, title FROM documents
               WHERE user_id = ? AND content_hash = ? AND status != 'failed'
               ORDER BY created_at LIMIT 1""",
            (user_id, markdown_hash),
        ) as cursor:
            return await cursor.fetchone()
