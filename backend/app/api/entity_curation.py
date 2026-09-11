"""Entity curation API: duplicate discovery + alias management (FEAT-025).

Own router under the shared /api/graph prefix (url_ingest/documents
precedent) — graph.py is at its size budget and these endpoints are a
distinct concern.

Route-matching note: `GET /entities/duplicates` coexists with
`PATCH/DELETE /entities/{entity_name:path}` because Starlette treats a
path-match with a method-mismatch as Match.PARTIAL, which does NOT
short-circuit the scan — the scan continues until a FULL match (the
existing `POST /entities/merge` under the same path params is the live
proof). Trade-off: the static segment "duplicates" is reserved — an entity
literally named "duplicates" is still reachable via its `/detail` suffix
route, same as today's "merge" reservation.
"""
import logging
from typing import List

from fastapi import APIRouter, Depends

from app.api.auth import get_current_user
from app.models.graph import (
    AliasDeleteRequest,
    EntityAliasesResponse,
    EntityDuplicatesResponse,
)
from app.services.entity_alias import (
    delete_alias,
    find_duplicate_groups,
    list_aliases_for,
    load_user_alias_map,
)
from app.services.neo4j_client import get_neo4j_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/graph", tags=["entity-curation"])

# Same safety cap the timeline endpoint uses — plenty for one user's graph.
_DUPLICATES_SCAN_LIMIT = 2000


@router.get("/entities/duplicates", response_model=EntityDuplicatesResponse)
async def get_duplicates(current_user: dict = Depends(get_current_user)):
    """Suggest probably-duplicate entity groups (case / punctuation variants).

    Purely advisory: groups are computed from the same entity+mention read
    the timeline uses; merging remains a manual, confirmed call to
    POST /api/graph/entities/merge. Names that are already alias keys are
    excluded — they were merged away and must not be re-suggested.
    """
    user_id = current_user["id"]
    neo4j = await get_neo4j_client()
    # NOTE: the client default is 200 — pass the explicit scan cap.
    rows = await neo4j.get_user_entities_with_mentions(
        user_id=user_id, limit=_DUPLICATES_SCAN_LIMIT,
    )
    alias_map = await load_user_alias_map(user_id)
    groups = find_duplicate_groups(rows, alias_keys=alias_map.keys())
    return EntityDuplicatesResponse(groups=groups, scanned=len(rows))


@router.get("/entities/{entity_name:path}/aliases", response_model=EntityAliasesResponse)
async def get_entity_aliases(
    entity_name: str,
    current_user: dict = Depends(get_current_user),
):
    """List the alias names recorded for one canonical entity."""
    user_id = current_user["id"]
    name = entity_name.strip()
    aliases = await list_aliases_for(user_id, name)
    return EntityAliasesResponse(entity=name, aliases=aliases)


@router.post("/aliases/delete")
async def delete_alias_endpoint(
    payload: AliasDeleteRequest,
    current_user: dict = Depends(get_current_user),
):
    """Unbind one alias (POST body, not a DELETE query param: entity names
    come from unconstrained LLM extraction and `+` in a query string decodes
    to a space — a JSON body has no encoding pitfalls).

    Unbinding does NOT split the already-merged graph; it only stops future
    ingestion/retrieval from resolving that name to the canonical.
    Idempotent: returns {"deleted": n} (0 when absent).
    """
    user_id = current_user["id"]
    deleted = await delete_alias(user_id, payload.alias.strip())
    return {"deleted": deleted}
