"""Retrieval scope resolution (FEAT-026).

Turns a request's optional ``tag`` / ``document_ids`` fields into the
effective document scope for ``retrieve(document_ids=...)``:

* ``None``          — no filter requested → search the whole library.
* non-empty list    — the only document_ids in scope (sorted, deduped).
* ``[]``            — the scope resolves to NOTHING (unknown tag, or a
  tag/document intersection with no overlap). Callers must treat this as
  "answer nothing": api/search.py short-circuits before the pipeline;
  retrieve() itself returns empty before the admission gate.

The tag path reads ``document_tags`` (same table the tags API manages);
matching uses the same normalisation as tag writes (strip, leading-#
peel, lowercase) so "#AI" in a request hits rows stored as "ai".

Kept free of the retrieval stack (mirrors entity_alias.py): both this
module and the tags API must agree on what "the same tag" means, but the
API layer must not become a service dependency.
"""
import logging
from typing import List, Optional, Sequence, Set

logger = logging.getLogger(__name__)


def _normalize_tag(raw: Optional[str]) -> Optional[str]:
    """Tag identity — mirror of api/documents.py normalize_tag (duplicated
    rather than imported to keep this module free of the API layer; the two
    MUST stay in sync: both define what "the same tag" means)."""
    if not raw:
        return None
    s = raw.strip()
    while s.startswith("#"):
        s = s[1:].lstrip()
    return s.lower() or None


async def resolve_document_ids_for_filter(
    user_id: int,
    tag: Optional[str] = None,
    document_ids: Optional[Sequence[str]] = None,
) -> Optional[List[str]]:
    """Resolve the effective document scope for one retrieval request."""
    if not tag and document_ids is None:
        return None

    by_tag: Optional[Set[str]] = None
    if tag:
        normalized = _normalize_tag(tag)
        if not normalized:
            return []
        from app.database import get_db

        async with get_db() as db:
            async with db.execute(
                "SELECT document_id FROM document_tags "
                "WHERE user_id = ? AND tag = ?",
                (user_id, normalized),
            ) as cur:
                rows = await cur.fetchall()
        by_tag = {r["document_id"] for r in rows}
        if not by_tag:
            # Unknown tag: an explicit empty scope, NOT None — widening an
            # unknown tag to the whole library would be the worst failure.
            return []

    by_ids: Optional[Set[str]] = None
    if document_ids is not None:
        by_ids = {d for d in document_ids if d and d.strip()}
        if not by_ids:
            return []

    if by_tag is not None and by_ids is not None:
        return sorted(by_tag & by_ids)
    return sorted(by_tag if by_tag is not None else (by_ids or set()))
