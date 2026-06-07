"""Tests for #14 document detail endpoint.

Standalone runner (no pytest needed):
    cd backend
    ../.venv/Scripts/python.exe tests/test_document_detail.py

Coverage:
  1. Neo4jClient.get_doc_entities — top N entities by MENTION count, scoped
     to the doc and user, with the mention_count per entity
  2. Neo4jClient.get_related_documents — other docs sharing entities with
     this one, ordered by shared count desc, NOT including the source doc
  3. API endpoint shape — GET /api/documents/{id}/detail returns the
     combined payload, 404 on missing/unowned doc
  4. User scoping — a doc belonging to user A returns 404 for user B
     (multi-tenant isolation)
  5. Empty state — a doc with no entities returns empty arrays, not nulls
"""
from __future__ import annotations

import asyncio
import sys
import unittest.mock as _mock
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


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
# Fake Neo4j — capture Cypher queries and return scripted results.
# =========================================================================

class _FakeRecord:
    """Mimics neo4j's record object — exposes .data() which returns the row dict."""
    def __init__(self, data):
        self._data = dict(data)
    def data(self):
        return dict(self._data)


class _FakeAsyncIter:
    def __init__(self, items):
        self._items = [(_FakeRecord(i) if isinstance(i, dict) else i)
                       for i in items]
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
    def __init__(self, scripted=None):
        self.scripted = list(scripted or [])
        self.calls: list = []
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
            payload = []
        if isinstance(payload, list):
            return _FakeAsyncIter(payload)
        return _mock.AsyncMock()


class _OneShotDriver:
    """Driver whose .session() returns the SAME fake instance every call.
    Good for tests that issue exactly one Cypher query per method."""
    def __init__(self, session):
        self._session = session

    def session(self):
        return self._session


# =========================================================================
# 1. Neo4jClient.get_doc_entities
# =========================================================================

def test_get_doc_entities_returns_top_n_by_mention_count():
    """The endpoint should surface the most-mentioned entities in the doc.
    Top 3 of {A:5, B:3, C:2, D:1} → [A, B, C] with their counts."""
    from app.services.neo4j_client import Neo4jClient

    client = Neo4jClient.__new__(Neo4jClient)
    captured = _FakeSession([[
        {"name": "Alice", "type": "PERSON", "mention_count": 5},
        {"name": "Bob",   "type": "PERSON", "mention_count": 3},
        {"name": "Cathy", "type": "ORG",    "mention_count": 2},
    ]])
    client._driver = _OneShotDriver(captured)

    out = asyncio.run(client.get_doc_entities(
        doc_id="doc-1", user_id=42, limit=3,
    ))
    check("get_doc_entities: 3 results", len(out) == 3)
    check("get_doc_entities: first is most-mentioned",
          out[0]["name"] == "Alice" and out[0]["mention_count"] == 5)
    check("get_doc_entities: each row carries type",
          all("type" in e for e in out))


def test_get_doc_entities_user_scoped():
    """The Cypher must filter on user_id and doc_id, and the limit must
    be passed through as a parameter."""
    from app.services.neo4j_client import Neo4jClient

    client = Neo4jClient.__new__(Neo4jClient)
    captured = _FakeSession([[]])
    client._driver = _OneShotDriver(captured)

    asyncio.run(client.get_doc_entities(doc_id="d", user_id=7, limit=5))
    cypher, params = captured.calls[0]
    check("get_doc_entities: user_id param set", params.get("user_id") == 7)
    check("get_doc_entities: doc_id param set", params.get("doc_id") == "d")
    check("get_doc_entities: limit param passed through",
          params.get("limit") == 5)


def test_get_doc_entities_handles_no_results():
    """A freshly-uploaded doc that hasn't been entity-extracted yet returns
    an empty list — not None, not an exception."""
    from app.services.neo4j_client import Neo4jClient

    client = Neo4jClient.__new__(Neo4jClient)
    captured = _FakeSession([[]])
    client._driver = _OneShotDriver(captured)

    out = asyncio.run(client.get_doc_entities(doc_id="d", user_id=1, limit=10))
    check("get_doc_entities: empty list, not None", out == [])


# =========================================================================
# 2. Neo4jClient.get_related_documents
# =========================================================================

def test_get_related_documents_excludes_source_doc():
    """A document can't be related to itself — the query must filter
    out the source doc from the results, and rank by shared_count desc."""
    from app.services.neo4j_client import Neo4jClient

    client = Neo4jClient.__new__(Neo4jClient)
    captured = _FakeSession([[
        {"doc_id": "doc-A", "title": "A", "shared_count": 4},
        {"doc_id": "doc-B", "title": "B", "shared_count": 2},
    ]])
    client._driver = _OneShotDriver(captured)

    out = asyncio.run(client.get_related_documents(
        doc_id="doc-SOURCE", user_id=42, limit=5,
    ))
    check("get_related_documents: 2 results", len(out) == 2)
    check("get_related_documents: highest shared count first",
          out[0]["doc_id"] == "doc-A" and out[0]["shared_count"] == 4)
    check("get_related_documents: source doc not in results",
          all(r["doc_id"] != "doc-SOURCE" for r in out))
    check("get_related_documents: result carries title",
          out[0].get("title") == "A")


def test_get_related_documents_user_scoped():
    from app.services.neo4j_client import Neo4jClient
    client = Neo4jClient.__new__(Neo4jClient)
    captured = _FakeSession([[]])
    client._driver = _OneShotDriver(captured)
    asyncio.run(client.get_related_documents(doc_id="d", user_id=99, limit=5))
    cypher, params = captured.calls[0]
    check("get_related_documents: user_id param set",
          params.get("user_id") == 99)
    check("get_related_documents: doc_id param set",
          params.get("doc_id") == "d")


# ------------------------------------------------------------------------
# Regression guard: the Cypher must traverse (:Document)-[:CONTAINS]->(:Chunk)
# rather than reference `c.document_id` as a property. The schema is
# edge-based (create_chunk_node never sets `c.document_id`), so a query
# that uses the property silently matches zero chunks and returns empty
# results. This was a real bug — caught by manual smoke testing in the
# detail page. These checks lock in the edge-based pattern so it can't
# regress unnoticed.
# ------------------------------------------------------------------------

def test_get_doc_entities_cypher_uses_contains_edge():
    from app.services.neo4j_client import Neo4jClient
    client = Neo4jClient.__new__(Neo4jClient)
    captured = _FakeSession([[]])
    client._driver = _OneShotDriver(captured)
    asyncio.run(client.get_doc_entities(doc_id="d", user_id=1, limit=5))
    cypher, _ = captured.calls[0]
    check("get_doc_entities: Cypher traverses (Document)-[:CONTAINS]->(Chunk)",
          ":Document" in cypher and "[:CONTAINS]" in cypher and ":Chunk" in cypher)
    check("get_doc_entities: Cypher does NOT reference c.document_id as a property",
          "c.document_id" not in cypher)


def test_get_related_documents_cypher_uses_contains_edge():
    from app.services.neo4j_client import Neo4jClient
    client = Neo4jClient.__new__(Neo4jClient)
    captured = _FakeSession([[]])
    client._driver = _OneShotDriver(captured)
    asyncio.run(client.get_related_documents(doc_id="d", user_id=1, limit=5))
    cypher, _ = captured.calls[0]
    check("get_related_documents: Cypher traverses both CONTAINS edges",
          cypher.count("[:CONTAINS]") >= 2)
    check("get_related_documents: Cypher does NOT reference any *.document_id",
          "c.document_id" not in cypher
          and "other.document_id" not in cypher
          and "src.document_id" not in cypher)


# =========================================================================
# 3. API endpoint shape
# =========================================================================

def test_api_router_exposes_detail_endpoint():
    """GET /api/documents/{doc_id}/detail must be registered on the router."""
    from app.api.documents import router

    methods_paths = sorted({
        (m, r.path)
        for r in router.routes if hasattr(r, "path") and hasattr(r, "methods")
        for m in (r.methods or set()) if m not in ("HEAD",)
    })
    check("/api/documents/{doc_id}/detail is registered",
          ("GET", "/api/documents/{doc_id}/detail") in methods_paths)


# =========================================================================
# 4. End-to-end detail endpoint (HTTP-level)
# =========================================================================

class _FakeRow(dict):
    """Row that supports BOTH dict-style and row_factory access."""
    def __getitem__(self, k): return super().__getitem__(k)
    def __contains__(self, k): return super().__contains__(k)


def _build_doc_ctx(doc_row=None, tags_rows=(), chunks_rows=(),
                    owner_user_id=None):
    """Stand-in for app.database.get_db(). Dispatches SQL results in
    a fixed order: doc → tags → chunk count → sample chunks.

    `owner_user_id`: when set, the fake simulates the ownership check
    on the doc-metadata query — if the params' user_id differs, that
    query returns an empty row set (mimicking the WHERE id=? AND user_id=?
    filter that the real SQL applies).
    """
    class _Cursor:
        def __init__(self, rows):
            self._rows = list(rows)
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def fetchall(self): return list(self._rows)
        async def fetchone(self):
            return self._rows[0] if self._rows else None

    _call_index = {"i": 0}

    class _Db:
        def __init__(self):
            self._queue = []
        def add(self, rows): self._queue.append(list(rows))
        def execute(self, sql, params=()):
            # NOTE: not `async def` — endpoint uses `async with db.execute(...)`
            # so this must return an async context manager instance, NOT a
            # coroutine. The real aiosqlite cursor has __aenter__/__aexit__.
            scripted = self._queue.pop(0) if self._queue else []
            if (owner_user_id is not None
                    and _call_index["i"] == 0
                    and len(params) >= 2
                    and params[-1] != owner_user_id):
                scripted = []
            _call_index["i"] += 1
            return _Cursor(scripted)
        async def commit(self): return None

    db = _Db()
    db.add([doc_row] if doc_row else [])  # 1. doc metadata
    db.add(tags_rows)                      # 2. tags
    db.add([{"n": len(chunks_rows)}])      # 3. chunk count
    db.add(chunks_rows[:3])                # 4. sample chunks

    class _Ctx:
        async def __aenter__(self): return db
        async def __aexit__(self, *a): return False

    return _Ctx()  # return an instance, not the class


def test_detail_endpoint_returns_combined_payload():
    """GET /documents/{id}/detail with a valid doc returns metadata +
    stats + key_entities + related + sample_chunks. Empty arrays for
    fields the doc has no data for."""
    from app.api import documents as docs_mod
    from app.services import neo4j_client as neo4j_mod
    from app.main import app
    from app.api import auth as auth_mod

    doc_row = _FakeRow({
        "id": "d-1", "title": "Hello", "original_filename": "hello.pdf",
        "file_type": "pdf", "file_size": 1024, "created_at": "2026-01-01",
    })
    tags_rows = [
        _FakeRow({"tag": "research"}),
        _FakeRow({"tag": "important"}),
    ]
    chunks_rows = [
        _FakeRow({"chunk_id": f"c{i}", "content": f"chunk {i}",
                  "hierarchy_path": "Section A > §A.1" if i == 0 else ""})
        for i in range(5)
    ]
    ctx = _build_doc_ctx(
        doc_row=doc_row, tags_rows=tags_rows, chunks_rows=chunks_rows,
    )

    with _mock.patch.object(docs_mod, "get_db", lambda: ctx), \
         _mock.patch.object(docs_mod, "get_neo4j_client", _mock.AsyncMock()) as neo4j_factory:

        neo4j = _mock.AsyncMock()
        neo4j.get_doc_entities = _mock.AsyncMock(return_value=[
            {"name": "Alice", "type": "PERSON", "mention_count": 3},
        ])
        neo4j.get_related_documents = _mock.AsyncMock(return_value=[
            {"doc_id": "d-2", "title": "Related", "shared_count": 2},
        ])
        neo4j_factory.return_value = neo4j

        app.dependency_overrides[auth_mod.get_current_user] = lambda: {"id": 1}
        try:
            from fastapi.testclient import TestClient
            client = TestClient(app)
            r = client.get("/api/documents/d-1/detail")
        finally:
            app.dependency_overrides.pop(auth_mod.get_current_user, None)

    check("GET detail: 200 OK", r.status_code == 200, f"got {r.status_code}: {r.text}")
    body = r.json()
    check("detail payload: has 'document' key", "document" in body)
    check("detail payload: document.id matches", body["document"]["id"] == "d-1")
    check("detail payload: document.title matches", body["document"]["title"] == "Hello")
    check("detail payload: document.tags is list of 2",
          isinstance(body["document"].get("tags"), list)
          and len(body["document"]["tags"]) == 2)
    check("detail payload: has 'stats' key", "stats" in body)
    check("detail payload: stats.chunk_count == 5", body["stats"]["chunk_count"] == 5)
    check("detail payload: has 'key_entities'",
          isinstance(body.get("key_entities"), list))
    check("detail payload: has 'related_documents'",
          isinstance(body.get("related_documents"), list))
    check("detail payload: has 'sample_chunks'",
          isinstance(body.get("sample_chunks"), list))
    check("detail payload: sample_chunks ≤ 3",
          len(body["sample_chunks"]) <= 3)
    if body["related_documents"]:
        check("detail payload: related_documents carries title",
              body["related_documents"][0]["title"] == "Related")


def test_detail_endpoint_404_for_missing_doc():
    """Asking for a non-existent doc returns 404, not 500."""
    from app.api import documents as docs_mod
    from app.services import neo4j_client as neo4j_mod
    from app.main import app
    from app.api import auth as auth_mod

    ctx = _build_doc_ctx()  # no doc row
    with _mock.patch.object(docs_mod, "get_db", lambda: ctx), \
         _mock.patch.object(neo4j_mod, "get_neo4j_client", _mock.AsyncMock()) as neo4j_factory:
        neo4j_factory.return_value = _mock.AsyncMock()
        app.dependency_overrides[auth_mod.get_current_user] = lambda: {"id": 1}
        try:
            from fastapi.testclient import TestClient
            client = TestClient(app)
            r = client.get("/api/documents/missing/detail")
        finally:
            app.dependency_overrides.pop(auth_mod.get_current_user, None)
    check("GET missing detail: 404", r.status_code == 404)


def test_detail_endpoint_user_scoped():
    """The detail endpoint must not leak another user's document. Even
    if you know the doc_id, you get 404 (we never expose ownership info
    via 401/403 because that's information leakage)."""
    from app.api import documents as docs_mod
    from app.services import neo4j_client as neo4j_mod
    from app.main import app
    from app.api import auth as auth_mod

    doc_row = _FakeRow({
        "id": "d-1", "title": "Private", "original_filename": "p.pdf",
        "file_type": "pdf", "file_size": 100, "created_at": "2026-01-01",
    })
    # Doc is owned by user 1, but we'll authenticate as user 999.
    # The fake mimics the WHERE user_id=? filter: anyone else's id
    # returns an empty row set on the metadata query.
    ctx = _build_doc_ctx(doc_row=doc_row, owner_user_id=1)
    with _mock.patch.object(docs_mod, "get_db", lambda: ctx), \
         _mock.patch.object(neo4j_mod, "get_neo4j_client", _mock.AsyncMock()) as neo4j_factory:
        neo4j_factory.return_value = _mock.AsyncMock()
        # Auth as user 999 — but the doc only exists for user 1
        app.dependency_overrides[auth_mod.get_current_user] = lambda: {"id": 999}
        try:
            from fastapi.testclient import TestClient
            client = TestClient(app)
            r = client.get("/api/documents/d-1/detail")
        finally:
            app.dependency_overrides.pop(auth_mod.get_current_user, None)
    check("cross-user access: 404 (no info leak)", r.status_code == 404)


# =========================================================================
# Driver
# =========================================================================

ALL_TESTS = [
    test_get_doc_entities_returns_top_n_by_mention_count,
    test_get_doc_entities_user_scoped,
    test_get_doc_entities_handles_no_results,
    test_get_related_documents_excludes_source_doc,
    test_get_related_documents_user_scoped,
    test_get_doc_entities_cypher_uses_contains_edge,
    test_get_related_documents_cypher_uses_contains_edge,
    test_api_router_exposes_detail_endpoint,
    test_detail_endpoint_returns_combined_payload,
    test_detail_endpoint_404_for_missing_doc,
    test_detail_endpoint_user_scoped,
]


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks for #14 document detail...")
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
