"""Entity alias storage + duplicate discovery (FEAT-025).

An alias row records "this merged-away name belongs to that canonical
entity". Three consumers:

* **Ingestion** (`documents._run_ingest_pipeline`): extracted names hitting
  the alias map are rewritten to their canonical BEFORE any Neo4j write, so
  a merged name never re-splits into a fresh node (create_entities_batch
  MERGEs on exact name — without the rewrite the old name resurrects).
* **Retriever graph channel**: query-extracted entity names are resolved
  through the map, because after a merge the old name no longer exists as a
  node and an exact-name lookup would silently miss.
* **API** (`entity_curation.py`, `graph.py`): merge auto-records the alias;
  the detail endpoint lists them; users can unbind.

All SQL lives in this module (eval/db_cases.py synchronous precedent).
Rows store the mapping as recorded; chain resolution (A→B, B→C ⇒ A→C)
happens at READ time via `_resolve_name`, which is why recording B→A after
A→B→C exists is a harmless idempotent no-op — write-time resolution makes
alias cycles unreachable.
"""
import dataclasses
import logging
import re
import sqlite3
from typing import Any, Dict, Iterable, List, Sequence

from app.config import get_settings

logger = logging.getLogger(__name__)


def _key(name: str) -> str:
    """Alias identity key — identical to entity_extractor._dedupe_key.

    (Duplicated rather than imported to keep this module free of the
    extraction stack; the two MUST stay in sync — both define what "the
    same name" means to the graph.)
    """
    return (name or "").strip().lower()


_PUNCT_RE = re.compile(r"[\W_]+", re.UNICODE)


def _punct_key(name: str) -> str:
    """Punctuation/space-insensitive key: "Open AI" / "Open-AI" / "OpenAI"
    all collapse. Word characters (letters, digits, CJK) survive."""
    return _PUNCT_RE.sub("", name or "").lower()


def _connect() -> sqlite3.Connection:
    """Synchronous stdlib connection (short-lived, per call)."""
    conn = sqlite3.connect(get_settings().SQLITE_PATH, timeout=5)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _resolve_name(name: str, mapping: Dict[str, str]) -> str:
    """Follow the alias chain to its final canonical.

    mapping keys are lowercased alias names, values canonical spellings.
    The visited-set guards against corrupt/cyclic rows (write-time
    resolution already prevents cycles from being recorded).
    """
    seen = set()
    cur = name
    while True:
        k = _key(cur)
        if not k or k in seen or k not in mapping:
            return cur
        seen.add(k)
        cur = mapping[k]


# =========================================================================
# Alias CRUD
# =========================================================================

async def record_alias(user_id: int, alias: str, canonical: str) -> bool:
    """Record `alias` → `canonical`. Returns True when a row was written.

    Semantics:
      * `canonical` is resolved through existing aliases first — recording
        B→A while A→B→C exists collapses to B→C (the already-recorded
        target), a no-op.
      * alias exactly equal to the resolved canonical (or either side
        blank) → no-op, False. Case-only variants ("openai" → "OpenAI")
        ARE recorded: node identity is the exact name, so the variant is a
        meaningful mapping, not a self-reference.
      * Re-recording an existing alias with the SAME effective canonical is
        an idempotent no-op (True); with a DIFFERENT one it is REFUSED
        (False) — unbind first, so chains can't be corrupted in one call.
    """
    alias_key = _key(alias)
    if not alias_key or not _key(canonical):
        return False
    mapping = await load_user_alias_map(user_id)
    final = _resolve_name(canonical.strip(), mapping) if mapping else canonical.strip()
    if alias.strip() == final:
        return False
    existing = mapping.get(alias_key)
    if existing is not None and _key(existing) != _key(final):
        logger.info(
            "record_alias: refusing rebind %r (existing %r -> %r) for user %d",
            alias, existing, final, user_id,
        )
        return False
    try:
        with _connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO entity_aliases (user_id, alias, canonical_name) "
                "VALUES (?, ?, ?)",
                (user_id, alias.strip(), final),
            )
            conn.commit()
        return True
    except sqlite3.Error as e:
        logger.warning("record_alias failed (user %d, %r): %s", user_id, alias, e)
        return False


async def delete_alias(user_id: int, alias: str) -> int:
    """Remove one alias mapping (matched on the stored name)."""
    try:
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM entity_aliases WHERE user_id = ? AND alias = ?",
                (user_id, alias),
            )
            conn.commit()
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    except sqlite3.Error as e:
        logger.warning("delete_alias failed (user %d, %r): %s", user_id, alias, e)
        return 0


async def delete_aliases_for(user_id: int, canonical_name: str) -> int:
    """Drop every alias pointing at `canonical_name` (entity-delete cleanup:
    a dangling canonical would resolve into silent misses forever)."""
    try:
        with _connect() as conn:
            cur = conn.execute(
                "DELETE FROM entity_aliases WHERE user_id = ? AND canonical_name = ?",
                (user_id, canonical_name),
            )
            conn.commit()
            return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    except sqlite3.Error as e:
        logger.warning("delete_aliases_for failed (user %d): %s", user_id, e)
        return 0


async def list_aliases_for(user_id: int, canonical_name: str) -> List[Dict[str, Any]]:
    """All aliases pointing at `canonical_name`, [{alias, created_at}]."""
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT alias, created_at FROM entity_aliases "
                "WHERE user_id = ? AND canonical_name = ? ORDER BY alias",
                (user_id, canonical_name),
            ).fetchall()
        return [{"alias": r["alias"], "created_at": r["created_at"]} for r in rows]
    except sqlite3.Error as e:
        logger.warning("list_aliases_for failed (user %d): %s", user_id, e)
        return []


async def load_user_alias_map(user_id: int) -> Dict[str, str]:
    """`{alias.lower(): canonical}` for one user — the resolution map.
    Keys are lowercased (lookup is case-insensitive); values keep the
    canonical's stored spelling. Rows are stored as recorded; chains are
    resolved at read time (see _resolve_name)."""
    try:
        with _connect() as conn:
            rows = conn.execute(
                "SELECT alias, canonical_name FROM entity_aliases WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        return {r[0].strip().lower(): r[1] for r in rows}
    except sqlite3.Error as e:
        logger.warning("load_user_alias_map failed (user %d): %s", user_id, e)
        return {}


async def prune_dangling_aliases(user_id: int) -> int:
    """Delete alias rows whose chain-resolved canonical no longer exists as
    a graph node.

    Needed because a document deletion can orphan entities INSIDE
    neo4j.delete_document (bypassing the delete-entity handler's alias
    cleanup), leaving aliases that resolve to nothing — every retrieval
    mentioning those names would silently miss the graph channel.
    Best-effort by construction (delete_alias swallows SQL errors).
    """
    from app.services.neo4j_client import get_neo4j_client

    mapping = await load_user_alias_map(user_id)
    if not mapping:
        return 0
    neo4j = await get_neo4j_client()
    rows = await neo4j.get_user_entities_with_mentions(user_id=user_id, limit=10000)
    existing = {(r.get("name") or "").strip().lower() for r in rows}
    removed = 0
    for alias in list(mapping.keys()):
        final = _key(_resolve_name(alias, mapping))
        if final and final not in existing:
            removed += await delete_alias(user_id, alias)
    return removed


async def resolve_names(names: Sequence[str], user_id: int) -> List[str]:
    """Resolve entity names through the user's alias map.

    Order-preserving and de-duplicated: several inputs may collapse onto one
    canonical, and `get_chunks_for_entities` gains nothing from repeats.
    """
    if not names:
        return []
    mapping = await load_user_alias_map(user_id)
    if not mapping:
        return list(names)
    out: List[str] = []
    seen: set = set()
    for name in names:
        resolved = _resolve_name(name, mapping)
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


# =========================================================================
# Extraction rewriting (pure, rebuild-style)
# =========================================================================

def apply_aliases_to_extraction(
    extraction_result: Dict[str, Any], alias_map: Dict[str, str]
) -> Dict[str, Any]:
    """Rewrite extracted entity names through the alias map.

    canonicalize_extraction_results hands us dataclass objects SHARED between
    `entities` and `chunk_entities`, and relations carry plain-string
    endpoints — so this is a pure REBUILD (never mutate in place): entities
    are recreated with rewritten names and de-duplicated on the new name
    (two extracted spellings can map onto one canonical; first-seen in list
    order wins with its own fields), chunk_entities are rebuilt to reference
    the rewritten entries, relation endpoints are remapped and post-remap
    self-loops are dropped (mirrors canonicalize).

    A canonical that exists only in the graph (not in this extraction) is
    fine: the renamed entry is kept, and create_entities_batch's ON MATCH
    just bumps updated_at without touching type.
    """
    if not alias_map:
        return extraction_result

    def mapped(name: str) -> str:
        return _resolve_name(name, alias_map)

    entities = []
    seen: set = set()
    for e in extraction_result.get("entities", []):
        new_name = mapped(e.name)
        if new_name in seen:
            continue
        seen.add(new_name)
        entities.append(dataclasses.replace(e, name=new_name))

    chunk_entities = []
    for cd in extraction_result.get("chunk_entities", []):
        chunk_seen: set = set()
        ents = []
        for e in cd.get("entities", []):
            new_name = mapped(e.name)
            if new_name in chunk_seen:
                continue
            chunk_seen.add(new_name)
            ents.append(dataclasses.replace(e, name=new_name))
        chunk_entities.append({**cd, "entities": ents})

    relations = []
    for r in extraction_result.get("relations", []):
        source = mapped(r.source)
        target = mapped(r.target)
        if source == target:
            continue
        relations.append(dataclasses.replace(r, source=source, target=target))

    return {"entities": entities, "relations": relations, "chunk_entities": chunk_entities}


# =========================================================================
# Duplicate discovery (pure)
# =========================================================================

def find_duplicate_groups(
    rows: Iterable[Dict[str, Any]], alias_keys: Iterable[str] = ()
) -> List[Dict[str, Any]]:
    """Group entities that are probably the same thing under different
    spellings. SUGGESTIONS only — merging stays a manual, confirmed action
    through POST /api/graph/entities/merge.

    Two rules:
      * case:  identical strip+lower key, ≥2 distinct exact names.
      * punct: identical punctuation/space-stripped key AND ≥2 distinct
        strip+lower keys that are not already fully covered by a case group
        (a pure case-variant pair is the first rule's finding).

    Names that are themselves alias keys are excluded — they were merged
    away; re-suggesting them would recommend re-merging history. Groups sort
    by total mention_count desc, capped at 100.
    """
    excluded = {k.strip().lower() for k in alias_keys}
    members = []
    for r in rows:
        name = (r.get("name") or "").strip()
        if not name or name.lower() in excluded:
            continue
        members.append({
            "name": name,
            "type": r.get("type") or "Unknown",
            "mention_count": int(r.get("mention_count") or 0),
            "doc_count": len(r.get("document_ids") or []),
        })
    by_name = {m["name"]: m for m in members}

    groups: List[Dict[str, Any]] = []

    # Rule 1: case variants.
    by_case: Dict[str, List[str]] = {}
    for m in members:
        by_case.setdefault(m["name"].lower(), []).append(m["name"])
    case_keys = {k for k, names in by_case.items() if len(names) >= 2}
    for key in case_keys:
        names = by_case[key]
        groups.append({
            "key": key, "reason": "case", "members": [by_name[n] for n in names],
        })

    # Rule 2: punctuation/space variants across distinct spellings. A pure
    # case pair collapses to ONE lower in punct space (set size 1 → skipped),
    # so no case-group exclusion is needed — and a mixed set (case pair plus
    # a punct variant, e.g. OpenAI/openai/Open AI) is correctly reported as
    # one whole group instead of losing the third member.
    by_punct: Dict[str, List[str]] = {}
    for m in members:
        by_punct.setdefault(_punct_key(m["name"]), []).append(m["name"])
    for key, names in by_punct.items():
        if len({n.lower() for n in names}) < 2:
            continue
        groups.append({
            "key": key, "reason": "punct",
            "members": [by_name[n] for n in sorted(set(names))],
        })

    # A case group fully contained in a punct group is redundant — the punct
    # group already proposes that merge (and more). Keep the larger view.
    punct_sets = [
        {m["name"] for m in g["members"]} for g in groups if g["reason"] == "punct"
    ]
    groups = [
        g for g in groups
        if g["reason"] != "case"
        or not any({m["name"] for m in g["members"]} <= ps for ps in punct_sets)
    ]

    groups.sort(key=lambda g: -sum(m["mention_count"] for m in g["members"]))
    return groups[:100]
