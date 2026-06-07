"""Tests for #8 graph-RAG retrieval.

Standalone runner (no pytest needed):
    cd backend
    ../.venv/Scripts/python.exe tests/test_graph_rag.py

Covers:
  1. Neo4jClient.get_chunks_for_entities — Cypher shape (user scoping,
     LIMIT clause, empty input handling)
  2. ChromaClient.get_chunks_by_ids — order preservation, missing-id
     tolerance
  3. Pydantic ChatRequest — use_graph_rag field, default value
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.chat import ChatRequest  # noqa: E402
from app.services.chroma_client import ChromaClient  # noqa: E402
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
# Fake session (shared shape with test_entity_curation.py)
# =========================================================================

class _FakeResult:
    """A result that supports BOTH `await result.single()` and
    `async for record in result` — mirrors the real neo4j AsyncResult.

    Construct with a list of row-dicts (use [] for empty, [dict] for one row).
    """

    def __init__(self, rows):
        self._rows = list(rows)

    async def single(self):
        return self._rows[0] if self._rows else None

    def __aiter__(self):
        return self._FakeAsyncIter(self._rows)

    class _FakeAsyncIter:
        def __init__(self, rows):
            self._rows = list(rows)
            self._i = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._i >= len(self._rows):
                raise StopAsyncIteration
            item = self._rows[self._i]
            self._i += 1
            return item


class _FakeCollection:
    """Minimal stand-in for chromadb's Collection with just .get()."""

    def __init__(self, by_id):
        # by_id: dict[chunk_id] -> (content, metadata)
        self._by_id = by_id
        self.calls: list = []

    def get(self, ids=None, where=None):
        self.calls.append({"ids": list(ids or []), "where": dict(where or {})})
        out_ids = []
        out_docs = []
        out_metas = []
        for cid in (ids or []):
            if cid in self._by_id:
                content, meta = self._by_id[cid]
                out_ids.append(cid)
                out_docs.append(content)
                out_metas.append(meta)
        return {"ids": out_ids, "documents": out_docs, "metadatas": out_metas}


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
        # Normalize: the real driver returns an AsyncResult that supports
        # both .single() and async-for. We pass a list of row-dicts.
        if payload is None:
            rows = []
        elif isinstance(payload, list):
            # If every element is a dict, treat as rows; otherwise wrap
            # the bare payload (preserves the test_entity_curation
            # behavior where a single dict = a single record).
            if all(isinstance(p, dict) for p in payload):
                rows = payload
            else:
                rows = [payload]
        else:
            rows = [payload]
        return _FakeResult(rows)


class _FakeDriver:
    def __init__(self, session):
        self._session = session

    def session(self):
        return self._session


# =========================================================================
# Pydantic model
# =========================================================================

def test_chat_request_default_use_graph_rag_is_false():
    """Without the field, the request defaults to the safe (off) behavior."""
    req = ChatRequest(message="hi")
    check("ChatRequest: use_graph_rag defaults to False",
          req.use_graph_rag is False)


def test_chat_request_explicit_true():
    req = ChatRequest(message="hi", use_graph_rag=True)
    check("ChatRequest: use_graph_rag=True round-trips", req.use_graph_rag is True)


# =========================================================================
# Neo4jClient.get_chunks_for_entities
# =========================================================================

def test_get_chunks_for_entities_empty_input_short_circuits():
    """Empty input must not even hit the database — saves a round-trip
    and avoids a Cypher syntax error from an empty IN list."""
    client = Neo4jClient.__new__(Neo4jClient)
    fake = _FakeSession()
    client._driver = _FakeDriver(fake)

    result = asyncio.run(client.get_chunks_for_entities([], user_id=1))
    check("get_chunks_for_entities: empty input returns []", result == [])
    check("get_chunks_for_entities: no query issued on empty input",
          len(fake.calls) == 0)


def test_get_chunks_for_entities_cypher_shape():
    # One run() call returns TWO rows: wrap them in a list-of-rows.
    # The outer list = one entry in the script (one run call), the inner
    # list = the rows to yield.
    fake = _FakeSession([
        [{"chunk_id": "c1"}, {"chunk_id": "c2"}],
    ])
    client = Neo4jClient.__new__(Neo4jClient)
    client._driver = _FakeDriver(fake)

    result = asyncio.run(client.get_chunks_for_entities(
        entity_names=["硅基流动", "Qwen"], user_id=42, limit=50,
    ))
    check("get_chunks_for_entities: returns chunk_ids in order",
          result == ["c1", "c2"])

    check("get_chunks_for_entities: one query issued", len(fake.calls) == 1)
    cypher, params = fake.calls[0]
    check("get_chunks_for_entities: targets (name IN $entity_names)",
          "e.name IN $entity_names" in cypher)
    check("get_chunks_for_entities: user-scoped (e.user_id = $user_id)",
          "e.user_id = $user_id" in cypher)
    check("get_chunks_for_entities: also user-scopes the chunk side",
          "c.user_id = $user_id" in cypher)
    check("get_chunks_for_entities: respects LIMIT clause",
          "LIMIT $limit" in cypher and params.get("limit") == 50)
    check("get_chunks_for_entities: passes entity_names as list",
          params.get("entity_names") == ["硅基流动", "Qwen"])
    check("get_chunks_for_entities: passes user_id",
          params.get("user_id") == 42)


def test_get_chunks_for_entities_handles_no_results():
    # Empty list of rows = no matches.
    fake = _FakeSession([[]])
    client = Neo4jClient.__new__(Neo4jClient)
    client._driver = _FakeDriver(fake)

    result = asyncio.run(client.get_chunks_for_entities(
        entity_names=["NoSuchEntity"], user_id=1,
    ))
    check("get_chunks_for_entities: empty result when no matches", result == [])


# =========================================================================
# ChromaClient.get_chunks_by_ids (via fake collection)
# =========================================================================

def test_get_chunks_by_ids_preserves_input_order():
    by_id = {
        "c1": ("content of c1", {"user_id": "1"}),
        "c2": ("content of c2", {"user_id": "1"}),
        "c3": ("content of c3", {"user_id": "1"}),
    }

    client = ChromaClient.__new__(ChromaClient)
    client._collection = _FakeCollection(by_id)

    result = client.get_chunks_by_ids(["c2", "c1", "c3"], user_id=1)
    check("get_chunks_by_ids: preserves input order",
          [c["chunk_id"] for c in result] == ["c2", "c1", "c3"])
    check("get_chunks_by_ids: returns 3 chunks", len(result) == 3)
    check("get_chunks_by_ids: chunk content is correct",
          result[0]["content"] == "content of c2")
    check("get_chunks_by_ids: chunk shape has chunk_id/content/metadata",
          set(result[0].keys()) == {"chunk_id", "content", "metadata"})


def test_get_chunks_by_ids_skips_missing_chunks():
    by_id = {
        "c1": ("content of c1", {"user_id": "1"}),
        "c3": ("content of c3", {"user_id": "1"}),
        # c2 intentionally absent — e.g. it was deleted
    }
    client = ChromaClient.__new__(ChromaClient)
    client._collection = _FakeCollection(by_id)

    result = client.get_chunks_by_ids(["c1", "c2", "c3"], user_id=1)
    check("get_chunks_by_ids: missing chunk silently dropped",
          [c["chunk_id"] for c in result] == ["c1", "c3"])


def test_get_chunks_by_ids_scopes_by_user_id():
    by_id = {
        "c1": ("content", {"user_id": "1"}),
    }
    coll = _FakeCollection(by_id)
    coll.calls = []
    client = ChromaClient.__new__(ChromaClient)
    client._collection = coll

    client.get_chunks_by_ids(["c1"], user_id=99)
    # The .get() call must include where={"user_id": "99"} so a chunk
    # belonging to user 1 cannot leak into user 99's response.
    check("get_chunks_by_ids: passes user_id=99 in where filter",
          coll.calls and coll.calls[0]["where"].get("user_id") == "99")


def test_get_chunks_by_ids_empty_input_no_call():
    coll = _FakeCollection({})
    coll.calls = []
    client = ChromaClient.__new__(ChromaClient)
    client._collection = coll

    result = client.get_chunks_by_ids([], user_id=1)
    check("get_chunks_by_ids: empty input returns []", result == [])
    check("get_chunks_by_ids: empty input skips collection call",
          len(coll.calls) == 0)


# =========================================================================
# Driver
# =========================================================================

ALL_TESTS = [
    test_chat_request_default_use_graph_rag_is_false,
    test_chat_request_explicit_true,
    test_get_chunks_for_entities_empty_input_short_circuits,
    test_get_chunks_for_entities_cypher_shape,
    test_get_chunks_for_entities_handles_no_results,
    test_get_chunks_by_ids_preserves_input_order,
    test_get_chunks_by_ids_skips_missing_chunks,
    test_get_chunks_by_ids_scopes_by_user_id,
    test_get_chunks_by_ids_empty_input_no_call,
]


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks for #8 graph-RAG...")
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
