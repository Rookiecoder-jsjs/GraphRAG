"""User-wide tag list endpoint.

Lives on its own router (not under /api/documents) so the URL `/api/tags`
reads naturally for the front-end's tag-cloud filter. Per-document tag
operations stay on the documents router.
"""
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query

from app.database import get_db
from app.api.auth import get_current_user
from app.models.document import TagResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tags", tags=["tags"])


@router.get("", response_model=list[TagResponse])
async def list_user_tags(
    current_user: dict = Depends(get_current_user),
    q: Annotated[
        Optional[str],
        Query(description="Optional case-insensitive substring filter"),
    ] = None,
):
    """Return all distinct tags the current user has used, with how many
    documents carry each tag. Sorted by count desc, then tag asc — the
    front-end renders this as a tag cloud where the most-used tags float
    to the top."""
    user_id = current_user["id"]

    if q:
        # LIKE is case-insensitive for ASCII by default in SQLite; for
        # non-ASCII we lowercase both sides via application code. We pass
        # a lowered q to be safe across all unicode.
        pattern = f"%{q.lower()}%"
        sql = """SELECT tag, COUNT(*) AS count
                 FROM document_tags
                 WHERE user_id = ? AND LOWER(tag) LIKE ?
                 GROUP BY tag
                 ORDER BY count DESC, tag ASC"""
        params = (user_id, pattern)
    else:
        sql = """SELECT tag, COUNT(*) AS count
                 FROM document_tags
                 WHERE user_id = ?
                 GROUP BY tag
                 ORDER BY count DESC, tag ASC"""
        params = (user_id,)

    async with get_db() as db:
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()

    return [TagResponse(tag=r["tag"], count=r["count"]) for r in rows]
