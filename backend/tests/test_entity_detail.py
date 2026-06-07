"""Tests for #6 entity detail page.

Standalone runner:
    cd backend
    ../.venv/Scripts/python.exe tests/test_entity_detail.py

Coverage:
  1. Neo4jClient.get_entity_detail signature + return shape
  2. The Cypher sent to Neo4j is user-scoped (binds user_id)
  3. API router registers GET /api/graph/entities/{name:path}/detail
  4. 404 when the entity doesn't exist for this user
  5. 200 with full envelope (entity, stats, documents, related, sample_chunks)
"""
from __future__ import annotations

import asyncio
import inspect
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
# Fakes — same shape as test_entity_curation.py
# =========================================================================

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


class _FakeResult:
    def __init__(self, record):
        self._record = record

    async def single(self):
        return self._record


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
            payload = None
        if isinstance(payload, list):
            return _FakeAsyncIter(payload)
        return _FakeResult(payload)


class _FakeDriver:
    def __init__(self, session):
        self._session = session

    def session(self):
        return self._session


# =========================================================================
# 1. Neo4jClient.get_entity_detail — signature
# =========================================================================

def test_get_entity_detail_signature():
    from app.services.neo4j_client import Neo4jClient
    sig = inspect.signature(Neo4jClient.get_entity_detail)
    params = sig.parameters
    check("get_entity_detail exists",
          hasattr(Neo4jClient, "get_entity_detail"))
    check("get_entity_detail is async",
          inspect.iscoroutinefunction(Neo4jClient.get_entity_detail))
    check("get_entity_detail has 'name' param", "name" in params)
    check("get_entity_detail has 'user_id' param", "user_id" in params)


def test_get_entity_detail_returns_none_when_entity_absent():
    """If the entity node doesn't exist for this user, the first query
    returns no row → we should propagate None so the API can 404."""
    from app.services.neo4j_client import Neo4jClient
    client = Neo4jClient.__new__(Neo4jClient)
    fake = _FakeSession(scripted=[None])  # no row
    client._driver = _FakeDriver(fake)
    result = asyncio.run(client.get_entity_detail(name="Ghost", user_id=1))
    check("get_entity_detail: returns None when entity not found",
          result is None)


def test_get_entity_detail_user_scopes_cypher():
    """The Cypher sent to Neo4j must include {user_id: $user_id} on the
    Entity match so we can't leak across users."""
    from app.services.neo4j_client import Neo4jClient
    client = Neo4jClient.__new__(Neo4jClient)
    # Scripted responses, one per `run()` call:
    #   1) entity row (single .single())
    #   2) stats row (single .single())
    #   3) documents list (async iter)
    #   4) related entities list (async iter)
    #   5) sample chunks list (async iter)
    # The fake returns a single-record result for dicts and an async
    # iterator for lists — that's what the production code expects.
    fake = _FakeSession(scripted=[
        {"name": "Foo", "type": "CONCEPT", "description": "x"},
        {"mention_count": 0, "document_count": 0, "related_entity_count": 0},
        [],  # documents
        [],  # related_entities
        [],  # sample_chunks
    ])
    client._driver = _FakeDriver(fake)
    asyncio.run(client.get_entity_detail(name="Foo", user_id=42))
    check("get_entity_detail: at least one Cypher was issued",
          len(fake.calls) >= 1)
    first_cypher, first_params = fake.calls[0]
    check("get_entity_detail: first query binds user_id",
          "user_id" in first_cypher)
    check("get_entity_detail: first query passes user_id=42",
          first_params.get("user_id") == 42)
    check("get_entity_detail: first query filters by name",
          first_params.get("name") == "Foo")
    # Every subsequent query must also scope to user_id — there's no way
    # to opt out, so this should hold for all calls.
    for i, (cypher, params) in enumerate(fake.calls):
        check(f"get_entity_detail: query {i} binds user_id",
              "user_id" in cypher)
        check(f"get_entity_detail: query {i} passes user_id=42",
              params.get("user_id") == 42)


def test_get_entity_detail_envelope_shape():
    """When the entity is found, every required envelope key is present
    (even when other lists are empty — the client always reads the same
    shape, no defensive checks)."""
    from app.services.neo4j_client import Neo4jClient
    client = Neo4jClient.__new__(Neo4jClient)
    # Scripted sequence (the implementation makes several run() calls):
    # 1) entity row  2) stats  3) documents  4) related  5) sample chunks
    fake = _FakeSession(scripted=[
        {"name": "QLoRA", "type": "CONCEPT", "description": "Quantized LoRA"},
        {"mention_count": 4, "document_count": 2, "related_entity_count": 3},
        [
            {"doc_id": "d1", "title": "LoRA paper", "chunk_count": 3,
             "first_seen": "2025-10-15"},
            {"doc_id": "d2", "title": "QLoRA blog", "chunk_count": 1,
             "first_seen": "2025-11-01"},
        ],
        [
            {"name": "LoRA", "type": "CONCEPT",
             "relation_type": "EXTENDS", "direction": "outgoing"},
            {"name": "BitsAndBytes", "type": "CONCEPT",
             "relation_type": "USES", "direction": "outgoing"},
        ],
        [
            {"chunk_id": "c1", "doc_id": "d1", "doc_title": "LoRA paper",
             "content_preview": "QLoRA is a quantized version of LoRA."},
        ],
    ])
    client._driver = _FakeDriver(fake)
    out = asyncio.run(client.get_entity_detail(name="QLoRA", user_id=1))
    check("get_entity_detail: returns a dict", isinstance(out, dict))
    for key in ("entity", "stats", "documents",
                "related_entities", "sample_chunks"):
        check(f"get_entity_detail: envelope has '{key}'", key in out)
    check("entity.name round-trips", out["entity"]["name"] == "QLoRA")
    check("entity.type round-trips", out["entity"]["type"] == "CONCEPT")
    check("stats.mention_count == 4", out["stats"]["mention_count"] == 4)
    check("stats.document_count == 2", out["stats"]["document_count"] == 2)
    check("documents has 2 entries", len(out["documents"]) == 2)
    check("related_entities has 2 entries", len(out["related_entities"]) == 2)
    check("sample_chunks has 1 entry", len(out["sample_chunks"]) == 1)
    check("related entry carries direction",
          out["related_entities"][0]["direction"] in ("outgoing", "incoming"))


# =========================================================================
# 2. API router
# =========================================================================

def test_detail_route_is_registered():
    """The endpoint must exist on the graph router at the expected path
    so the frontend can fetch /api/graph/entities/{name}/detail."""
    from app.api.graph import router
    paths = {r.path for r in router.routes}
    candidates = [p for p in paths if p.endswith("/entities/{entity_name:path}/detail")]
    check("graph router: GET /entities/{name:path}/detail registered",
          len(candidates) == 1)


# =========================================================================
# 3. Endpoint behavior (with mocked Neo4j + get_current_user)
# =========================================================================

def test_endpoint_404_when_entity_not_found():
    """If Neo4j returns None (entity missing or another user's data),
    the API responds 404 — never leaks existence to other users."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.api import auth as auth_mod, graph as graph_mod

    fake_client = _mock.AsyncMock()
    fake_client.get_entity_detail = _mock.AsyncMock(return_value=None)

    async def _stub_get_neo4j():
        return fake_client

    app = create_app()
    # FastAPI's `app.dependency_overrides` is the proper way to swap a
    # Depends() target at test time — module-level patches don't work
    # because FastAPI captures the reference when it builds the route.
    app.dependency_overrides[auth_mod.get_current_user] = \
        lambda: {"id": 1}
    with _mock.patch.object(graph_mod, "get_neo4j_client", _stub_get_neo4g_unused):
        # The override path is taken instead of the module patch.
        pass
    # Use a direct monkey-patch (the override alone is enough for auth;
    # for get_neo4j_client the test calls into the same app, so we also
    # need to swap the import in graph_mod).
    with _mock.patch.object(graph_mod, "get_neo4j_client", _stub_get_neo4j), \
         TestClient(app) as client:
        resp = client.get("/api/graph/entities/Missing/detail")
    app.dependency_overrides.clear()
    check("endpoint: 404 when entity not found", resp.status_code == 404)


def _stub_get_neo4g_unused():
    # Sentinel — only used to keep the import-from-attribute path
    # working when dependency_overrides is the auth path. (No-op.)
    raise RuntimeError("this stub should never be called")


def test_endpoint_200_with_full_envelope():
    """Happy path: Neo4j returns a full detail envelope → 200 with that
    envelope. The endpoint should NOT mutate the response shape — the
    client depends on the keys being in the documented positions."""
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.api import auth as auth_mod, graph as graph_mod

    envelope = {
        "entity": {"name": "QLoRA", "type": "CONCEPT",
                   "description": "Quantized LoRA"},
        "stats": {"mention_count": 4, "document_count": 2,
                  "related_entity_count": 3},
        "documents": [
            {"doc_id": "d1", "title": "LoRA paper", "chunk_count": 3,
             "first_seen": "2025-10-15"},
        ],
        "related_entities": [
            {"name": "LoRA", "type": "CONCEPT",
             "relation_type": "EXTENDS", "direction": "outgoing"},
        ],
        "sample_chunks": [
            {"chunk_id": "c1", "doc_id": "d1", "doc_title": "LoRA paper",
             "content_preview": "QLoRA is ..."},
        ],
    }
    fake_client = _mock.AsyncMock()
    fake_client.get_entity_detail = _mock.AsyncMock(return_value=envelope)

    async def _stub_get_neo4j():
        return fake_client

    app = create_app()
    app.dependency_overrides[auth_mod.get_current_user] = \
        lambda: {"id": 1}
    with _mock.patch.object(graph_mod, "get_neo4j_client", _stub_get_neo4j), \
         TestClient(app) as client:
        resp = client.get("/api/graph/entities/QLoRA/detail")
    app.dependency_overrides.clear()

    check("endpoint: 200 on success", resp.status_code == 200)
    if resp.status_code == 200:
        body = resp.json()
        check("endpoint: body has 'entity' key", "entity" in body)
        check("endpoint: body has 'stats' key", "stats" in body)
        check("endpoint: body has 'documents' key", "documents" in body)
        check("endpoint: body has 'related_entities' key",
              "related_entities" in body)
        check("endpoint: body has 'sample_chunks' key",
              "sample_chunks" in body)


# =========================================================================
# Driver
# =========================================================================

# =========================================================================

# Regression guard: every Cypher in get_entity_detail that joins Chunk
# to Document must traverse the `[:CONTAINS]` edge, NOT reference
# `c.document_id` as a property. The schema is edge-based; the original
# queries used the property and silently returned empty/0 for the
# "Mentioned in" / sample-chunks / stats blocks. This guard records all
# Cypher strings issued by get_entity_detail and asserts on the shape.
# =========================================================================

def test_entity_detail_cypher_uses_contains_edge():
    from app.services.neo4j_client import Neo4jClient

    client = Neo4jClient.__new__(Neo4jClient)
    captured = []

    # Per-call scripted responses. get_entity_detail issues 5 run() calls:
    #   1. entity row          → .single()
    #   2. stats row           → .single()
    #   3. documents list      → async iter
    #   4. related entities    → async iter (but uses both outgoing and
    #                            incoming queries; we collapse to 4 here)
    #   5. sample chunks       → async iter
    # We only care about Cypher TEXT, not values, so empty lists and
    # stub dicts are fine.
    call_idx = {"n": 0}
    responses = [
        {"name": "X", "type": "PERSON", "description": ""},    # 1 entity
        {"mention_count": 0, "document_count": 0,
         "related_entity_count": 0},                              # 2 stats
        [],                                                       # 3 docs
        [],                                                       # 4 related
        [],                                                       # 5 samples
    ]

    class _FakeResult:
        def __init__(self, payload):
            self._payload = payload
        async def single(self):
            return self._payload
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration

    class _Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def run(self, cypher, **params):
            captured.append(str(cypher))
            payload = (responses[call_idx["n"]]
                       if call_idx["n"] < len(responses) else None)
            call_idx["n"] += 1
            # `iter([])` is a sync fallback; the prod code calls
            # .single() OR async-iter, never both on the same result.
            return _FakeResult(payload or {})

    class _Driver:
        def session(self): return _Session()

    client._driver = _Driver()
    asyncio.run(client.get_entity_detail(
        name="X", user_id=1, sample_chunk_limit=5, related_limit=10,
    ))
    check("get_entity_detail: issues >= 5 Cypher queries",
          len(captured) >= 5)
    joined = "\n".join(captured)
    check("get_entity_detail: traverses (:Document)-[:CONTAINS]->(Chunk)",
          "[:CONTAINS]" in joined)
    check("get_entity_detail: does NOT reference c.document_id anywhere",
          "c.document_id" not in joined)

ALL_TESTS = [
    test_get_entity_detail_signature,
    test_get_entity_detail_returns_none_when_entity_absent,
    test_get_entity_detail_user_scopes_cypher,
    test_get_entity_detail_envelope_shape,
    test_detail_route_is_registered,
    test_endpoint_404_when_entity_not_found,
    test_endpoint_200_with_full_envelope,
    test_entity_detail_cypher_uses_contains_edge,
]

def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks...")
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
