"""User dashboard summary endpoint.

Bundles everything the landing page needs into a single round-trip:

  * Hero stats: documents, chunks, entities, relations, conversations,
    messages, tags — all user-scoped counts.
  * Recent activity: a merged, time-sorted list of the last 10 things
    the user did (uploads + chat messages) so the user can pick up
    where they left off.
  * Top entities: top 10 entities by mention count, plus their type.
  * Top tags: top 10 tags by usage count.
  * Monthly growth: last 6 months of document uploads (for a small
    inline bar chart).

The response is intentionally a single object — the front-end makes
one request when the user lands on the dashboard.
"""
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.database import get_db
from app.api.auth import get_current_user
from app.services.neo4j_client import get_neo4j_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


# =========================================================================
# Response models
# =========================================================================

class HeroStats(BaseModel):
    documents: int = 0
    chunks: int = 0
    entities: int = 0
    relations: int = 0
    conversations: int = 0
    messages: int = 0
    tags: int = 0


class ActivityItem(BaseModel):
    kind: str  # "document" or "message"
    id: str
    title: str
    created_at: datetime
    # message-only:
    conversation_id: Optional[str] = None
    conversation_title: Optional[str] = None
    role: Optional[str] = None


class TopEntity(BaseModel):
    name: str
    type: str
    mention_count: int
    doc_count: int


class TopTag(BaseModel):
    tag: str
    count: int


class MonthBucket(BaseModel):
    month: str  # "2025-10"
    count: int


class DashboardSummary(BaseModel):
    stats: HeroStats
    recent_activity: List[ActivityItem] = []
    top_entities: List[TopEntity] = []
    top_tags: List[TopTag] = []
    growth: List[MonthBucket] = []


# =========================================================================
# Endpoint
# =========================================================================

@router.get("/summary", response_model=DashboardSummary)
async def get_dashboard_summary(current_user: dict = Depends(get_current_user)):
    """Build the dashboard summary. Reads SQLite + Neo4j in parallel-ish
    (sequential, but each is fast and user-scoped)."""
    user_id = current_user["id"]
    summary = DashboardSummary(stats=HeroStats())

    # ---- 1. Hero stats (SQLite) ----
    async with get_db() as db:
        # Documents
        async with db.execute(
            "SELECT COUNT(*) AS n FROM documents WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        summary.stats.documents = int(row["n"]) if row else 0

        # Chunks
        async with db.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        summary.stats.chunks = int(row["n"]) if row else 0

        # Conversations
        async with db.execute(
            "SELECT COUNT(*) AS n FROM conversations WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        summary.stats.conversations = int(row["n"]) if row else 0

        # Messages — join via conversations to enforce user ownership
        async with db.execute(
            """SELECT COUNT(*) AS n FROM messages m
               INNER JOIN conversations c ON c.id = m.conversation_id
               WHERE c.user_id = ?""",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        summary.stats.messages = int(row["n"]) if row else 0

        # Distinct tag count
        async with db.execute(
            "SELECT COUNT(DISTINCT tag) AS n FROM document_tags WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        summary.stats.tags = int(row["n"]) if row else 0

    # ---- 2. Entity / relation counts (Neo4j) ----
    neo4j = await get_neo4j_client()
    summary.stats.entities = await neo4j.count_user_entities(user_id)
    summary.stats.relations = await neo4j.count_user_relations(user_id)

    # ---- 3. Recent activity — merge uploads + messages, sort by created_at desc ----
    activity: List[ActivityItem] = []

    async with get_db() as db:
        # 8 most recent documents
        async with db.execute(
            """SELECT id, title, original_filename, created_at
               FROM documents WHERE user_id = ?
               ORDER BY created_at DESC LIMIT 8""",
            (user_id,),
        ) as cursor:
            doc_rows = await cursor.fetchall()
    for r in doc_rows:
        activity.append(ActivityItem(
            kind="document",
            id=r["id"],
            title=r["title"] or r["original_filename"],
            created_at=r["created_at"],
        ))

    async with get_db() as db:
        # 8 most recent messages (with their conversation's title)
        async with db.execute(
            """SELECT m.id AS msg_id, m.conversation_id, m.role, m.content, m.created_at,
                      c.title AS conv_title
               FROM messages m
               INNER JOIN conversations c ON c.id = m.conversation_id
               WHERE c.user_id = ?
               ORDER BY m.created_at DESC LIMIT 8""",
            (user_id,),
        ) as cursor:
            msg_rows = await cursor.fetchall()
    for r in msg_rows:
        # The activity feed is a glance — truncate long message bodies.
        snippet = (r["content"] or "").strip().replace("\n", " ")
        if len(snippet) > 80:
            snippet = snippet[:77] + "..."
        activity.append(ActivityItem(
            kind="message",
            id=str(r["msg_id"]),
            title=snippet or "(empty message)",
            created_at=r["created_at"],
            conversation_id=r["conversation_id"],
            conversation_title=r["conv_title"],
            role=r["role"],
        ))

    # Sort merged list, take 10 newest
    activity.sort(key=lambda a: a.created_at, reverse=True)
    summary.recent_activity = activity[:10]

    # ---- 4. Top entities (Neo4j) ----
    top_entities_raw = await neo4j.get_user_entities_with_mentions(
        user_id=user_id, limit=10
    )
    summary.top_entities = [
        TopEntity(
            name=e["name"],
            type=e["type"],
            mention_count=int(e.get("mention_count") or 0),
            # doc_count = number of distinct document_ids
            doc_count=len([d for d in (e.get("document_ids") or []) if d]),
        )
        for e in top_entities_raw
    ]

    # ---- 5. Top tags (SQLite) ----
    async with get_db() as db:
        async with db.execute(
            """SELECT tag, COUNT(*) AS count
               FROM document_tags WHERE user_id = ?
               GROUP BY tag
               ORDER BY count DESC, tag ASC LIMIT 10""",
            (user_id,),
        ) as cursor:
            tag_rows = await cursor.fetchall()
    summary.top_tags = [
        TopTag(tag=r["tag"], count=r["count"]) for r in tag_rows
    ]

    # ---- 6. Monthly growth (last 6 months) ----
    # Build the list server-side so the front-end can render a bar chart
    # without having to fill empty months itself.
    async with get_db() as db:
        async with db.execute(
            """SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS count
               FROM documents WHERE user_id = ?
                 AND created_at >= date('now', '-6 months')
               GROUP BY month
               ORDER BY month""",
            (user_id,),
        ) as cursor:
            growth_rows = await cursor.fetchall()
    raw = {r["month"]: r["count"] for r in growth_rows if r["month"]}
    # Fill missing months with 0 so the chart's x-axis is contiguous.
    now = datetime.now()
    buckets: List[MonthBucket] = []
    for offset in range(5, -1, -1):
        # 5 months ago, 4 months ago, ..., this month
        y = now.year
        m = now.month - offset
        while m <= 0:
            m += 12
            y -= 1
        key = f"{y:04d}-{m:02d}"
        buckets.append(MonthBucket(month=key, count=raw.get(key, 0)))
    summary.growth = buckets

    return summary
