"""Regression tests for the Neo4j delete fixes.

Standalone runner (no pytest needed):
    cd backend
    ../.venv/Scripts/python.exe tests/test_neo4j_delete_fix.py

Coverage:
  1. delete_document must only DETACH DELETE *orphaned* entities (those no
     longer mentioned by any remaining chunk of the same user) — it must
     NO LONGER unconditionally delete every RELATES_TO edge of the doc's
     entities (that wiped relationships of entities still alive via other
     documents). DETACH DELETE on the orphan handles its own RELATES_TO
     cleanup, so no separate Step 5 is issued.
  2. delete_document Steps 1 / 3 / 6 now carry the user_id filter (defence
     in depth beyond the API-layer ownership check).
  3. The three entity-mutation endpoints (PATCH / DELETE / merge) call
     invalidate_retrieval_cache(user_id) on success, so cached retrieval
     results never reference a stale entity graph.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from fastapi import HTTPException  # noqa: E402

from app.models.graph import UpdateEntityRequest, MergeEntityRequest  # noqa: E402
from app.services.neo4j_client import Neo4jClient  # noqa: E402


# =========================================================================
# Standalone runner
# =========================================================================

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_failures: list = []


def check(name: str, cond: bool, detail: str = ""):
    status = PASS if cond else FAIL
    suffix = f" — {detail}" if detail and not cond else ""
    print(f"  [{status}] {name}{suffix}")
    if not cond:
        _failures.append(name)


# =========================================================================
# Fake Neo4j session — records run() calls so we can assert on the Cypher.
# Same shape as test_entity_curation.py.
# =========================================================================

class _FakeResult:
    def __init__(self, record):
        self._record = record

    async def single(self):
        return self._record


class _FakeSession:
    """Async context-manager session that captures every run() call.

    `scripted` is a list of return values (one per run() call); each entry
    is a dict (returned via .single()). None means "no rows".
    """

    def __init__(self, scripted=None):
        self.scripted = list(scripted or [])
        self.calls: list = []  # (cypher_str, params_dict)
        self._index = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def run(self, cypher, **params):
        self.calls.append((str(cypher).strip(), params))
        if self._index < len(self.scripted):
            payload = self.scripted[self._index]
            self._index += 1
        else:
            payload = None
        return _FakeResult(payload)


class _FakeDriver:
    def __init__(self, session):
        self._session = session

    def session(self):
        return self._session


def _make_client_with_fake_session(scripted):
    """Build a Neo4jClient whose session() returns a fake that records run() calls."""
    client = Neo4jClient.__new__(Neo4jClient)
    fake = _FakeSession(scripted)
    client._driver = _FakeDriver(fake)
    return client, fake


# =========================================================================
# delete_document — orphan-only DETACH DELETE (no unconditional RELATES_TO)
# =========================================================================

# Happy path: doc exists, 2 chunks, 2 entities mentioned, 1 orphan + 1 survivor.
_HAPPY_SCRIPT = [
    {"count": 5},   # before-delete entity count
    {"count": 3},   # before-delete chunk count
    {"d": {"doc_id": "doc1", "title": "T"}, "chunk_ids": ["c1", "c2"]},  # Step 1
    {"entity_names": ["A", "B"]},  # Step 2
    {"deleted": 2},  # Step 3 (MENTIONS)
    {"deleted": 1},  # Step 4 (orphan entities, incl. their RELATES_TO)
    {"deleted": 1},  # Step 6 (document + chunks)
    {"count": 4},    # final entity count
]


def _orphan_queries(calls):
    """The run() calls whose Cypher DETACH DELETEs an Entity (`e`)."""
    return [c for c in calls if "DETACH DELETE e" in c[0]]


def test_delete_document_orphans_only_detach_deleted():
    client, fake = _make_client_with_fake_session(_HAPPY_SCRIPT)
    asyncio.run(client.delete_document(doc_id="doc1", user_id=42))
    check("delete_document: happy path issues 8 queries",
          len(fake.calls) == 8)

    # Exactly one query DETACH DELETEs an Entity (Step 4). Step 6 deletes
    # the document/chunks (`DETACH DELETE d, c`) — a different target.
    orphans = _orphan_queries(fake.calls)
    check("delete_document: exactly one orphan-entity DETACH DELETE",
          len(orphans) == 1)

    # The old buggy Step 5 wiped ALL RELATES_TO edges touching this doc's
    # entities even when those entities stayed alive in other docs. It is
    # gone: no query may contain RELATES_TO at all.
    check("delete_document: no unconditional RELATES_TO deletion (Step 5 removed)",
          not any("RELATES_TO" in cypher for cypher, _ in fake.calls),
          "found a RELATES_TO query")

    orphan_cypher, orphan_params = orphans[0]
    # Only entities with ZERO remaining referencing chunks are deleted.
    check("delete_document: orphan query gates on size(referencing_chunks) = 0",
          "WHERE size(referencing_chunks) = 0" in orphan_cypher)
    # The orphan check is scoped to the SAME user's chunks — a mention from
    # another user must not block the delete (nor leak across tenants).
    check("delete_document: orphan OPTIONAL MATCH filtered by c.user_id",
          "OPTIONAL MATCH (e)<-[r:MENTIONS]-(c:Chunk)" in orphan_cypher
          and "c.user_id = $user_id" in orphan_cypher)
    check("delete_document: orphan query passes user_id + entity_names params",
          orphan_params.get("user_id") == 42
          and orphan_params.get("entity_names") == ["A", "B"])


def test_delete_document_no_relates_to_delete_even_with_relations():
    # Even when the doc's entities are connected by RELATES_TO edges in the
    # DB, the generated Cypher must never delete those edges directly — the
    # orphan DETACH DELETE handles them only for entities that are actually
    # removed. (This is the regression the fix targets.)
    client, fake = _make_client_with_fake_session(_HAPPY_SCRIPT)
    asyncio.run(client.delete_document(doc_id="doc1", user_id=42))
    check("delete_document: no query deletes RELATES_TO edges",
          not any("RELATES_TO" in cypher and "DELETE" in cypher
                  for cypher, _ in fake.calls))


def test_delete_document_all_queries_pass_user_id():
    # Every run() call — stats, Steps 1/2/3/4/6 — is scoped to the user.
    client, fake = _make_client_with_fake_session(_HAPPY_SCRIPT)
    asyncio.run(client.delete_document(doc_id="doc1", user_id=42))
    check("delete_document: 8 queries total",
          len(fake.calls) == 8)
    for i, (_, params) in enumerate(fake.calls):
        check(f"delete_document: query {i} passes user_id=42",
              params.get("user_id") == 42)
    # Step 1 (index 2) filters the Document node by user_id in the pattern.
    check("delete_document: Step 1 pattern scopes Document by user_id",
          "user_id: $user_id" in fake.calls[2][0])
    # Step 3 (index 4) filters the MENTIONS delete by chunk.user_id.
    check("delete_document: Step 3 scopes MENTIONS delete by c.user_id",
          "c.user_id = $user_id" in fake.calls[4][0])
    # Step 6 (index 6) filters the document/chunk detach by user_id.
    check("delete_document: Step 6 pattern scopes detach by user_id",
          "user_id: $user_id" in fake.calls[6][0])


def test_delete_document_missing_doc_emits_no_destructive_queries():
    # Doc not found: only the two before-delete stats + the after-delete
    # stats run; nothing touches MENTIONS / entities / RELATES_TO.
    client, fake = _make_client_with_fake_session([
        {"count": 5},
        {"count": 3},
        {"d": None, "chunk_ids": []},  # Step 1 → not found
        {"count": 5},                  # after-delete stats
    ])
    asyncio.run(client.delete_document(doc_id="nope", user_id=42))
    check("delete_document: missing doc runs only stats queries",
          len(fake.calls) == 4)
    check("delete_document: missing doc issues no orphan DETACH DELETE",
          not _orphan_queries(fake.calls))
    check("delete_document: missing doc issues no MENTIONS delete",
          not any("MENTIONS" in cypher for cypher, _ in fake.calls))
    check("delete_document: missing doc issues no RELATES_TO delete",
          not any("RELATES_TO" in cypher for cypher, _ in fake.calls))
    # The not-found Step 1 still carried the user_id filter.
    check("delete_document: missing-doc Step 1 scoped by user_id",
          "user_id: $user_id" in fake.calls[2][0])


def test_delete_document_no_entities_skips_orphan_delete():
    # Doc with chunks but NO entities mentioned: Step 4 (orphan cleanup) is
    # skipped entirely; only MENTIONS + document/chunk delete run.
    client, fake = _make_client_with_fake_session([
        {"count": 5},
        {"count": 3},
        {"d": {"doc_id": "doc1"}, "chunk_ids": ["c1"]},  # Step 1
        {"entity_names": []},  # Step 2 → nothing mentioned
        {"deleted": 1},        # Step 3
        {"deleted": 1},        # Step 6
        {"count": 4},          # final
    ])
    asyncio.run(client.delete_document(doc_id="doc1", user_id=42))
    check("delete_document: no-entity doc issues 7 queries",
          len(fake.calls) == 7)
    check("delete_document: no-entity doc skips orphan DETACH DELETE",
          not _orphan_queries(fake.calls))
    check("delete_document: no-entity doc still deletes document + chunks",
          any("DETACH DELETE d, c" in cypher for cypher, _ in fake.calls))


# =========================================================================
# Entity-mutation endpoints invalidate the retrieval cache on success
# =========================================================================

def _patch_neo4j(fake_methods: dict):
    """Context manager: patch get_neo4j_client with a fake client whose
    async methods return the given results."""
    client = MagicMock()
    for name, ret in fake_methods.items():
        setattr(client, name, AsyncMock(return_value=ret))
    return patch(
        "app.api.graph.get_neo4j_client",
        new=AsyncMock(return_value=client),
    )


def test_update_entity_invalidates_retrieval_cache():
    from app.api.graph import update_entity
    with _patch_neo4j({"update_entity": {
            "name": "Foo", "type": "PERSON", "description": "Edited",
    }}):
        with patch("app.api.graph.invalidate_retrieval_cache") as inv:
            result = asyncio.run(update_entity(
                entity_name="Foo",
                payload=UpdateEntityRequest(entity_type="PERSON"),
                current_user={"id": 7},
            ))
    check("update_entity: returns the updated row", result["name"] == "Foo")
    check("update_entity: invalidates retrieval cache with user_id=7",
          inv.call_count == 1 and inv.call_args.args == (7,))


def test_update_entity_not_found_does_not_invalidate():
    from app.api.graph import update_entity
    with _patch_neo4j({"update_entity": None}):
        with patch("app.api.graph.invalidate_retrieval_cache") as inv:
            raised = False
            try:
                asyncio.run(update_entity(
                    entity_name="Missing",
                    payload=UpdateEntityRequest(entity_type="PERSON"),
                    current_user={"id": 7},
                ))
            except HTTPException as e:
                raised = e.status_code == 404
    check("update_entity: 404 on missing row", raised)
    check("update_entity: no cache invalidation on 404", inv.call_count == 0)


def test_delete_entity_invalidates_retrieval_cache():
    from app.api.graph import delete_entity
    with _patch_neo4j({"delete_entity": 1}):
        with patch("app.api.graph.invalidate_retrieval_cache") as inv:
            result = asyncio.run(delete_entity(
                entity_name="Foo", current_user={"id": 7},
            ))
    check("delete_entity: 204 returns None", result is None)
    check("delete_entity: invalidates retrieval cache with user_id=7",
          inv.call_count == 1 and inv.call_args.args == (7,))


def test_merge_entities_invalidates_retrieval_cache():
    from app.api.graph import merge_entities
    with _patch_neo4j({"merge_entities": {
            "merged_from": "A", "merged_into": "B",
            "mentions_rewritten": 2, "outgoing_relations_rewritten": 1,
            "incoming_relations_rewritten": 0, "source_deleted": 1,
    }}):
        with patch("app.api.graph.invalidate_retrieval_cache") as inv:
            result = asyncio.run(merge_entities(
                payload=MergeEntityRequest(source="A", target="B"),
                current_user={"id": 7},
            ))
    check("merge_entities: returns the count summary",
          result["merged_from"] == "A" and result["source_deleted"] == 1)
    check("merge_entities: invalidates retrieval cache with user_id=7",
          inv.call_count == 1 and inv.call_args.args == (7,))


def test_merge_entities_missing_source_does_not_invalidate():
    from app.api.graph import merge_entities
    client = MagicMock()
    client.merge_entities = AsyncMock(side_effect=LookupError("source entity not found: 'A'"))
    with patch("app.api.graph.get_neo4j_client", new=AsyncMock(return_value=client)):
        with patch("app.api.graph.invalidate_retrieval_cache") as inv:
            raised = False
            try:
                asyncio.run(merge_entities(
                    payload=MergeEntityRequest(source="A", target="B"),
                    current_user={"id": 7},
                ))
            except HTTPException as e:
                raised = e.status_code == 404
    check("merge_entities: missing source raises 404", raised)
    check("merge_entities: no cache invalidation on failure", inv.call_count == 0)


# =========================================================================
# Driver
# =========================================================================

ALL_TESTS = [
    test_delete_document_orphans_only_detach_deleted,
    test_delete_document_no_relates_to_delete_even_with_relations,
    test_delete_document_all_queries_pass_user_id,
    test_delete_document_missing_doc_emits_no_destructive_queries,
    test_delete_document_no_entities_skips_orphan_delete,
    test_update_entity_invalidates_retrieval_cache,
    test_update_entity_not_found_does_not_invalidate,
    test_delete_entity_invalidates_retrieval_cache,
    test_merge_entities_invalidates_retrieval_cache,
    test_merge_entities_missing_source_does_not_invalidate,
]


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks for the Neo4j delete fixes...")
    for fn in ALL_TESTS:
        try:
            fn()
        except Exception as e:
            check(f"{fn.__name__}: no unhandled exceptions", False, repr(e))
    print()
    if _failures:
        print(f"{FAIL} {len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print(f"{PASS} All checks passed ({len(ALL_TESTS)} tests).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
