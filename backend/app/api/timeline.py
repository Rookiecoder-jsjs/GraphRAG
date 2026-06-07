"""User timeline / chronology endpoint.

Combines:
  * SQLite `documents.created_at` for the upload chronology
  * Neo4j `Entity` nodes + `MENTIONS` edges to derive "first seen" per
    entity — i.e. when an entity first appeared in the user's knowledge
    base, and which document introduced it.

The response shape is small on purpose so the front-end can render a
simple bar chart + chronological lists without further joins.
"""
import logging
from datetime import date, datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.database import get_db
from app.api.auth import get_current_user
from app.services.neo4j_client import get_neo4j_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/timeline", tags=["timeline"])


# =========================================================================
# Response models
# =========================================================================

class DocumentsByMonth(BaseModel):
    month: str  # "2025-10" — sortable as a string
    count: int


class RecentDocument(BaseModel):
    id: str
    title: Optional[str] = None
    original_filename: str
    created_at: datetime


class EntityTimelineItem(BaseModel):
    name: str
    type: str
    first_seen: Optional[date] = None
    first_seen_doc_id: Optional[str] = None
    first_seen_doc_title: Optional[str] = None
    doc_count: int
    mention_count: int


class TimelineResponse(BaseModel):
    documents_by_month: List[DocumentsByMonth] = []
    recent_documents: List[RecentDocument] = []
    entity_timeline: List[EntityTimelineItem] = []


# =========================================================================
# Helpers
# =========================================================================

def _to_date(value) -> Optional[date]:
    """SQLite gives us a string like '2025-10-15 10:00:00'. Extract
    just the date portion so the front-end can group cleanly."""
    if not value:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        # Try ISO date-only first, then fall back to "YYYY-MM-DD HH:MM:SS".
        for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value[:len(fmt) + 4], fmt).date()
            except ValueError:
                continue
    return None


def _first_seen(items):
    """Return the (date, doc_id, doc_title) of the earliest item by
    created_at, where each item is (doc_id, doc_title, created_at_str).
    Returns (None, None, None) for empty input."""
    if not items:
        return None, None, None
    # min() on a string of "YYYY-MM-DD..." sorts chronologically.
    items_sorted = sorted(items, key=lambda x: x[2] or "")
    doc_id, doc_title, created = items_sorted[0]
    return _to_date(created), doc_id, doc_title


# =========================================================================
# Endpoint
# =========================================================================

@router.get("", response_model=TimelineResponse)
async def get_timeline(current_user: dict = Depends(get_current_user)):
    """Build the timeline. Three independent reads (SQLite, Neo4j,
    SQLite again for the document-date join) — no transactions, all
    reads are user-scoped, and the work is bounded by the per-user
    data set (a few hundred entities at most)."""
    user_id = current_user["id"]
    response = TimelineResponse()

    # ---- 1. Document upload volume, by month ----
    async with get_db() as db:
        async with db.execute(
            """
            SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS count
            FROM documents WHERE user_id = ?
            GROUP BY month
            ORDER BY month
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    response.documents_by_month = [
        DocumentsByMonth(month=r["month"], count=r["count"])
        for r in rows if r["month"]
    ]

    # ---- 2. Recent documents (last 10, by created_at desc) ----
    async with get_db() as db:
        async with db.execute(
            """
            SELECT id, title, original_filename, created_at
            FROM documents WHERE user_id = ?
            ORDER BY created_at DESC LIMIT 10
            """,
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
    response.recent_documents = [
        RecentDocument(
            id=r["id"],
            title=r["title"],
            original_filename=r["original_filename"],
            created_at=r["created_at"],
        )
        for r in rows
    ]

    # ---- 3. Entity timeline ----
    # Get all entities with their chunk_ids and document_ids, then join
    # with SQLite to get document created_at and title.
    neo4j = await get_neo4j_client()
    entities = await neo4j.get_user_entities_with_mentions(user_id=user_id, limit=500)

    if entities:
        # Collect all unique document_ids we need to look up.
        all_doc_ids = set()
        for e in entities:
            for did in (e.get("document_ids") or []):
                if did:
                    all_doc_ids.add(did)

        doc_meta: dict = {}
        if all_doc_ids:
            placeholders = ",".join("?" * len(all_doc_ids))
            async with get_db() as db:
                async with db.execute(
                    f"""SELECT id, title, created_at FROM documents
                        WHERE user_id = ? AND id IN ({placeholders})""",
                    (user_id, *all_doc_ids),
                ) as cursor:
                    rows = await cursor.fetchall()
            doc_meta = {r["id"]: (r["title"], r["created_at"]) for r in rows}

        items: List[EntityTimelineItem] = []
        for e in entities:
            doc_ids = [d for d in (e.get("document_ids") or []) if d in doc_meta]
            if not doc_ids:
                # Entity exists but every doc that mentioned it has been
                # deleted — we still want to surface the entity (it might
                # come back), but with no "first seen" date and a 0 count.
                items.append(EntityTimelineItem(
                    name=e["name"],
                    type=e["type"],
                    first_seen=None,
                    first_seen_doc_id=None,
                    first_seen_doc_title=None,
                    doc_count=0,
                    mention_count=int(e.get("mention_count") or 0),
                ))
                continue

            triples = []
            for did in doc_ids:
                title, created = doc_meta[did]
                triples.append((did, title, created))

            first_date, first_id, first_title = _first_seen(triples)
            items.append(EntityTimelineItem(
                name=e["name"],
                type=e["type"],
                first_seen=first_date,
                first_seen_doc_id=first_id,
                first_seen_doc_title=first_title,
                doc_count=len(doc_ids),
                mention_count=int(e.get("mention_count") or 0),
            ))

        # Newest first — the "topics emerging" angle is the most useful
        # way to read a knowledge base. Ties (same date) fall back to
        # mention_count desc, then name asc.
        items.sort(
            key=lambda x: (
                x.first_seen is None,                # None goes last
                -(x.first_seen.toordinal() if x.first_seen else 0),
                -x.mention_count,
                x.name,
            )
        )
        # Trim to top 200 — even power users rarely have meaningful first-seen
        # data for more than that, and the response stays small.
        response.entity_timeline = items[:200]

    return response
