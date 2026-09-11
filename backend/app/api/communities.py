"""Graph community endpoints (FEAT-028 global search).

Shares the /api/graph prefix as its own router module (entity_curation
precedent) — communities are graph-domain but live in their own module so
graph.py doesn't keep growing. Registered in main.py next to the other
/api/graph routers.

Rebuild is the one LLM-heavy write path in the graph domain: ONE
summarization call per community (usually well under COMMUNITY_MAX=20).
It deliberately does NOT take a query-gate slot — the gate is the
retrieval admission queue (QUERY_CONCURRENCY=4) and holding a slot for a
minutes-long rebuild would starve chat/search. Protection instead comes
from a dedicated sliding-window limiter plus the semaphore inside
build_communities.
"""
import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.api.auth import get_current_user
from app.auth.rate_limit import enforce_rate_limit
from app.database import get_db

router = APIRouter(prefix="/api/graph", tags=["communities"])

logger = logging.getLogger(__name__)


@router.get("/communities")
async def list_communities(current_user: dict = Depends(get_current_user)):
    """All communities for the user, ranked by total mentions, plus a
    staleness flag: the graph has grown/shrunk since the last build when
    the current entity count no longer matches what was built against."""
    user_id = current_user["id"]
    rows = await _load_rows(user_id)

    stale = False
    if rows:
        try:
            from app.services.neo4j_client import get_neo4j_client

            neo4j = await get_neo4j_client()
            current_count = await neo4j.count_user_entities(user_id)
            stale = any(
                (r["entity_count_at_build"] or 0) != current_count for r in rows
            )
        except Exception as e:  # noqa: BLE001 — staleness is best-effort
            logger.warning("community staleness check failed: %s", e)

    return {
        "stale": stale,
        "communities": [
            {
                "id": r["id"],
                "title": r["title"],
                "summary": r["summary"],
                "members": _parse_members(r["member_names"]),
                "member_count": r["member_count"],
                "mention_total": r["mention_total"],
                "entity_count_at_build": r["entity_count_at_build"],
                "created_at": r["created_at"],
            }
            for r in rows
        ],
    }


@router.post("/communities/rebuild")
async def rebuild_communities(current_user: dict = Depends(get_current_user)):
    """Re-run community detection + summarization for this user."""
    from app.auth.rate_limit import communities_limiter
    from app.services.graph_community import build_communities

    user_id = current_user["id"]
    enforce_rate_limit(communities_limiter, f"communities:{user_id}")
    return await build_communities(user_id)


async def _load_rows(user_id: int) -> List[Dict[str, Any]]:
    async with get_db() as db:
        async with db.execute(
            """SELECT id, title, summary, member_names, member_count,
                      mention_total, entity_count_at_build, created_at
               FROM graph_communities WHERE user_id = ?
               ORDER BY mention_total DESC, id ASC""",
            (user_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


def _parse_members(raw: str) -> List[str]:
    import json

    try:
        members = json.loads(raw or "[]")
        return members if isinstance(members, list) else []
    except ValueError:
        return []
