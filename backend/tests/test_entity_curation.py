"""Tests for #7 entity curation (PATCH / DELETE / merge).

Standalone runner (no pytest needed):
    cd backend
    ../.venv/Scripts/python.exe tests/test_entity_curation.py

Coverage:
  1. Pydantic request model validation (the contract between frontend
     and backend)
  2. Neo4jClient method signatures and basic behavior — using a fake
     session that records Cypher queries, so we verify the shape of
     what's sent to the database without needing a live Neo4j
  3. API router surface — endpoints registered with the right methods
     and paths

The merge query logic itself runs against a real Neo4j; here we just
verify the *shape* (right node labels, right user_id filter, right
merge pattern). Real merge correctness is covered by manual smoke
testing once a live DB is available.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# pydantic v2 raises ValidationError
from pydantic import ValidationError

from app.models.graph import (  # noqa: E402
    UpdateEntityRequest,
    MergeEntityRequest,
)
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
# =========================================================================

class _FakeResult:
    def __init__(self, record):
        self._record = record

    async def single(self):
        return self._record


class _FakeAsyncIter:
    def __init__(self, items):
        self._items = list(items)
        self._i = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._i >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._i]
        self._i += 1
        return item


class _FakeSession:
    """Async context-manager session that captures every run() call.

    `scripted` is a list of return values (one per run() call). Each entry
    is either a dict (returned as a single record via .single()) or a list
    of dicts (returned as multiple records via async iteration). None
    means "no rows".
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
        if isinstance(payload, list):
            return _FakeAsyncIter(payload)
        return _FakeResult(payload)


class _FakeDriver:
    """Driver whose session() returns the same fake instance every call."""

    def __init__(self, session):
        self._session = session

    def session(self):
        return self._session


# =========================================================================
# Pydantic model tests
# =========================================================================

def test_update_entity_accepts_empty_body():
    # Both fields optional → empty body is valid (the router rejects this
    # with 400, but the model itself is permissive).
    req = UpdateEntityRequest()
    check("UpdateEntityRequest: empty body is valid",
          req.entity_type is None and req.description is None)


def test_update_entity_accepts_partial():
    req = UpdateEntityRequest(entity_type="PERSON")
    check("UpdateEntityRequest: entity_type only",
          req.entity_type == "PERSON" and req.description is None)

    req = UpdateEntityRequest(description="A test entity")
    check("UpdateEntityRequest: description only",
          req.description == "A test entity" and req.entity_type is None)


def test_update_entity_accepts_empty_description_string():
    # Empty string is a meaningful value (clear the description); not None.
    req = UpdateEntityRequest(description="")
    check("UpdateEntityRequest: empty-string description is preserved (not coerced to None)",
          req.description == "")


def test_merge_entity_requires_source_and_target():
    req = MergeEntityRequest(source="A", target="B")
    check("MergeEntityRequest: minimal valid body",
          req.source == "A" and req.target == "B")


def test_merge_entity_rejects_empty_strings():
    for kwargs in ({"source": "", "target": "B"}, {"source": "A", "target": ""}):
        raised = False
        try:
            MergeEntityRequest(**kwargs)
        except ValidationError:
            raised = True
        check(f"MergeEntityRequest: rejects empty string in {kwargs}", raised)


def test_merge_entity_allows_same_name_at_model_layer():
    # The model is permissive — the router / Neo4jClient enforce source != target.
    # (This test documents that the Pydantic layer does NOT add a validator
    # for it; the rule is enforced where it matters.)
    req = MergeEntityRequest(source="X", target="X")
    check("MergeEntityRequest: allows source == target (router enforces)",
          req.source == req.target)


# =========================================================================
# Neo4jClient method tests (using fake session to capture Cypher shape)
# =========================================================================

def _make_client_with_fake_session(scripted):
    """Build a Neo4jClient whose session() returns a fake that records run() calls."""
    client = Neo4jClient.__new__(Neo4jClient)
    fake = _FakeSession(scripted)
    client._driver = _FakeDriver(fake)
    return client, fake


def test_update_entity_queries_with_user_id_and_returns_row():
    client, fake = _make_client_with_fake_session(
        [{"name": "Foo", "type": "PERSON", "description": "Updated"}]
    )
    result = asyncio.run(client.update_entity(
        name="Foo", user_id=42, entity_type="PERSON", description="Updated",
    ))
    check("update_entity: returns the row",
          result == {"name": "Foo", "type": "PERSON", "description": "Updated"})
    check("update_entity: single session.run() call", len(fake.calls) == 1)
    cypher, params = fake.calls[0]
    check("update_entity: targets (name, user_id) pair",
          "{name: $name, user_id: $user_id}" in cypher)
    check("update_entity: passes entity_type param",
          params.get("entity_type") == "PERSON")
    check("update_entity: passes description param",
          params.get("description") == "Updated")


def test_update_entity_skips_unset_fields():
    client, fake = _make_client_with_fake_session(
        [{"name": "Foo", "type": "PERSON", "description": ""}]
    )
    asyncio.run(client.update_entity(
        name="Foo", user_id=42, entity_type="PERSON", description=None,
    ))
    cypher, params = fake.calls[0]
    # description=None means "leave alone" → must not appear in SET clause or params
    check("update_entity: unset description excluded from params",
          "description" not in params)
    # The SET form is "e.description = $description" — match specifically,
    # not just the bare "e.description" token (which appears in RETURN).
    check("update_entity: SET does not include e.description assignment",
          "e.description = $description" not in cypher)


def test_update_entity_treats_empty_string_as_clear():
    client, fake = _make_client_with_fake_session(
        [{"name": "Foo", "type": "PERSON", "description": ""}]
    )
    asyncio.run(client.update_entity(
        name="Foo", user_id=42, entity_type="PERSON", description="",
    ))
    cypher, params = fake.calls[0]
    # Empty string gets converted to None for the DB write so the column
    # is genuinely cleared (SQLite-style).
    check("update_entity: empty string → None in params",
          params.get("description") is None)
    check("update_entity: SET includes e.description when clearing",
          "e.description = $description" in cypher)


def test_update_entity_returns_none_when_not_found():
    client, fake = _make_client_with_fake_session([None])
    result = asyncio.run(client.update_entity(
        name="Missing", user_id=1, entity_type="PERSON", description="x",
    ))
    check("update_entity: returns None on missing row", result is None)


def test_delete_entity_emits_three_queries():
    # delete_entity issues 3 queries; only the last one's RETURN is read.
    # The first two are bare `await session.run(...)` calls that ignore
    # their return value, so script None for them.
    client, fake = _make_client_with_fake_session([None, None, {"deleted": 1}])
    deleted = asyncio.run(client.delete_entity(name="Foo", user_id=42))
    check("delete_entity: returns 1 on success", deleted == 1)
    check("delete_entity: 3 queries (MENTIONS / RELATES_TO / entity)",
          len(fake.calls) == 3)
    # MENTIONS first, RELATES_TO second, entity third
    check("delete_entity: first query deletes MENTIONS",
          "MENTIONS" in fake.calls[0][0])
    check("delete_entity: second query deletes RELATES_TO",
          "RELATES_TO" in fake.calls[1][0])
    check("delete_entity: third query deletes the entity",
          "DELETE e" in fake.calls[2][0])
    # All three are user-scoped
    for i, (_, params) in enumerate(fake.calls):
        check(f"delete_entity: query {i} is user-scoped",
              params.get("user_id") == 42)
        check(f"delete_entity: query {i} targets the right name",
              params.get("name") == "Foo")


def test_delete_entity_returns_zero_on_missing():
    client, fake = _make_client_with_fake_session([{"deleted": 0}])
    deleted = asyncio.run(client.delete_entity(name="Missing", user_id=1))
    check("delete_entity: returns 0 on missing", deleted == 0)


def test_merge_entities_rejects_same_name():
    client, fake = _make_client_with_fake_session([])
    raised = False
    try:
        asyncio.run(client.merge_entities(
            source_name="A", target_name="A", user_id=1,
        ))
    except ValueError:
        raised = True
    check("merge_entities: source == target raises ValueError", raised)
    check("merge_entities: no queries issued on validation failure",
          len(fake.calls) == 0)


def test_merge_entities_emits_existence_check_first():
    # Scripted: existence check returns both names; then MENTIONS / out / in / delete
    client, fake = _make_client_with_fake_session([
        [{"name": "A"}, {"name": "B"}],
        {"removed": 3},  # MENTIONS
        {"removed": 2},  # outgoing RELATES_TO
        {"removed": 1},  # incoming RELATES_TO
        {"deleted": 1},  # source entity delete
    ])
    result = asyncio.run(client.merge_entities(
        source_name="A", target_name="B", user_id=42,
    ))
    check("merge_entities: returns count summary",
          result["merged_from"] == "A"
          and result["merged_into"] == "B"
          and result["mentions_rewritten"] == 3
          and result["outgoing_relations_rewritten"] == 2
          and result["incoming_relations_rewritten"] == 1
          and result["source_deleted"] == 1)
    check("merge_entities: 5 queries total (existence + 4 ops)",
          len(fake.calls) == 5)
    # First query is the existence check
    cypher0, _ = fake.calls[0]
    check("merge_entities: first query is existence check",
          "e.name IN [$source, $target]" in cypher0)


def test_merge_entities_raises_404_style_for_missing_source():
    # Existence check returns only target → LookupError for source
    client, fake = _make_client_with_fake_session([
        [{"name": "B"}],
    ])
    raised = False
    try:
        asyncio.run(client.merge_entities(
            source_name="A", target_name="B", user_id=1,
        ))
    except LookupError as e:
        raised = "source" in str(e)
    check("merge_entities: missing source raises LookupError", raised)
    # Should have stopped after existence check — no destructive queries
    check("merge_entities: no destructive queries when source missing",
          len(fake.calls) == 1)


def test_merge_entities_raises_for_missing_target():
    client, fake = _make_client_with_fake_session([
        [{"name": "A"}],
    ])
    raised = False
    try:
        asyncio.run(client.merge_entities(
            source_name="A", target_name="B", user_id=1,
        ))
    except LookupError as e:
        raised = "target" in str(e)
    check("merge_entities: missing target raises LookupError", raised)
    check("merge_entities: no destructive queries when target missing",
          len(fake.calls) == 1)


def test_merge_entities_all_queries_user_scoped():
    client, fake = _make_client_with_fake_session([
        [{"name": "A"}, {"name": "B"}],
        {"removed": 0}, {"removed": 0}, {"removed": 0}, {"deleted": 1},
    ])
    asyncio.run(client.merge_entities("A", "B", user_id=99))
    for i, (_, params) in enumerate(fake.calls):
        check(f"merge_entities: query {i} scoped to user_id=99",
              params.get("user_id") == 99)


# =========================================================================
# API router tests
# =========================================================================

def test_api_endpoints_registered():
    from app.api.graph import router
    methods_paths = sorted({
        (m, r.path)
        for r in router.routes if hasattr(r, "path") and hasattr(r, "methods")
        for m in (r.methods or set()) if m not in ("HEAD",)
    })

    expected = {
        ("GET", "/api/graph/entities"),
        ("POST", "/api/graph/query"),
        ("GET", "/api/graph/visualization"),
        ("PATCH", "/api/graph/entities/{entity_name:path}"),
        ("DELETE", "/api/graph/entities/{entity_name:path}"),
        ("POST", "/api/graph/entities/merge"),
    }
    # Both are sets now; subtraction is symmetric and gives the missing entries.
    missing = set(expected) - set(methods_paths)
    check(f"graph router exposes all expected endpoints (missing: {missing})",
          not missing)


# =========================================================================
# Driver
# =========================================================================

ALL_TESTS = [
    test_update_entity_accepts_empty_body,
    test_update_entity_accepts_partial,
    test_update_entity_accepts_empty_description_string,
    test_merge_entity_requires_source_and_target,
    test_merge_entity_rejects_empty_strings,
    test_merge_entity_allows_same_name_at_model_layer,
    test_update_entity_queries_with_user_id_and_returns_row,
    test_update_entity_skips_unset_fields,
    test_update_entity_treats_empty_string_as_clear,
    test_update_entity_returns_none_when_not_found,
    test_delete_entity_emits_three_queries,
    test_delete_entity_returns_zero_on_missing,
    test_merge_entities_rejects_same_name,
    test_merge_entities_emits_existence_check_first,
    test_merge_entities_raises_404_style_for_missing_source,
    test_merge_entities_raises_for_missing_target,
    test_merge_entities_all_queries_user_scoped,
    test_api_endpoints_registered,
]


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks for #7 entity curation...")
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
