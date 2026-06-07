"""Tests for #12 timeline / chronology view.

Standalone runner:
    cd backend
    ../.venv/Scripts/python.exe tests/test_timeline.py

Coverage:
  1. _to_date / _first_seen helpers (pure)
  2. Empty user — all three lists are empty
  3. documents_by_month — SQLite strftime grouping
  4. recent_documents — last 10, ordered by created_at desc
  5. entity_timeline — joined with SQLite for first_seen date
  6. entity_timeline sort — newest first_seen first, None goes last
  7. entity whose mentioning documents have been deleted — first_seen
     is None and the entity is still listed (mention_count preserved)
  8. User isolation — query params and Neo4j user_id
"""
from __future__ import annotations

import asyncio
import sys
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
# Pure-function tests
# =========================================================================

def test_to_date_from_iso_string():
    from app.api.timeline import _to_date
    check("to_date: '2025-10-15' -> date(2025, 10, 15)",
          _to_date("2025-10-15") is not None
          and str(_to_date("2025-10-15")) == "2025-10-15")


def test_to_date_from_sqlite_timestamp():
    """SQLite gives us 'YYYY-MM-DD HH:MM:SS' — strip the time, keep the date."""
    from app.api.timeline import _to_date
    d = _to_date("2025-10-15 14:32:11")
    check("to_date: '2025-10-15 14:32:11' -> date(2025, 10, 15)",
          d is not None and str(d) == "2025-10-15")


def test_to_date_from_iso_timestamp():
    from app.api.timeline import _to_date
    d = _to_date("2025-10-15T14:32:11")
    check("to_date: '2025-10-15T14:32:11' -> date(2025, 10, 15)",
          d is not None and str(d) == "2025-10-15")


def test_to_date_handles_invalid_and_empty():
    from app.api.timeline import _to_date
    check("to_date: None -> None", _to_date(None) is None)
    check("to_date: '' -> None", _to_date("") is None)
    check("to_date: 'not a date' -> None", _to_date("not a date") is None)


def test_first_seen_picks_earliest():
    from app.api.timeline import _first_seen
    items = [
        ("doc-b", "Beta", "2025-11-01 09:00:00"),
        ("doc-a", "Alpha", "2025-10-15 14:00:00"),
        ("doc-c", "Gamma", "2025-12-20 18:00:00"),
    ]
    d, did, title = _first_seen(items)
    check("first_seen: returns earliest date",
          d is not None and str(d) == "2025-10-15")
    check("first_seen: returns the doc_id of the earliest",
          did == "doc-a")
    check("first_seen: returns the title of the earliest",
          title == "Alpha")


def test_first_seen_empty_input():
    from app.api.timeline import _first_seen
    d, did, title = _first_seen([])
    check("first_seen: empty -> (None, None, None)",
          d is None and did is None and title is None)


# =========================================================================
# Fake DB driver
# =========================================================================
#
# Aiosqlite surface: get_db() is an async-context-manager; db.execute()
# returns an object that is BOTH awaitable and an async context manager
# (production code uses whichever is convenient); the cursor's
# `await fetchall()` returns the row list.

class _FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def fetchall(self):
        return list(self._rows)


class _FakeResult:
    """BOTH awaitable and async context manager — production code uses
    `await db.execute(...)` for INSERT/DELETE and `async with db.execute(...) as cursor:`
    for SELECT."""
    def __init__(self, cursor):
        self._cursor = cursor

    def __await__(self):
        async def _():
            return self._cursor
        return _().__await__()

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *a):
        return False


class _FakeDb:
    def __init__(self, scripted_rows_per_execute):
        self._scripted = [list(r) for r in scripted_rows_per_execute]
        self.execute_calls: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.execute_calls.append((sql, tuple(params)))
        rows = self._scripted.pop(0) if self._scripted else []
        return _FakeResult(_FakeCursor(rows))

    async def commit(self):
        pass


def _make_db(scripted):
    db = _FakeDb(scripted)
    def _get_db():
        return db
    return _get_db, db


# =========================================================================
# Fake Neo4j client
# =========================================================================

class _FakeNeo4j:
    """Stands in for `await get_neo4j_client()`. We only need
    `get_user_entities_with_mentions` for the timeline endpoint."""
    def __init__(self, entities):
        self._entities = entities
        self.calls: list = []

    async def get_user_entities_with_mentions(self, user_id, limit=200):
        self.calls.append((user_id, limit))
        return self._entities


def _make_neo4j(entities):
    n = _FakeNeo4j(entities)
    async def _get():
        return n
    return _get, n


# =========================================================================
# Endpoint tests
# =========================================================================

def _stub_user(user_id: int = 1):
    async def _dep():
        return {"id": user_id, "username": "alice"}
    return _dep


async def _call(coro):
    try:
        result = await coro
        return ("ok", None, result)
    except Exception as e:
        return ("err", type(e).__name__, getattr(e, "detail", str(e)))


def test_empty_user_returns_empty_lists():
    """A new user with zero documents and zero entities gets a
    TimelineResponse with three empty lists — not None, not a 404."""
    from app.api import timeline as tl_mod
    import unittest.mock as _mock
    get_db, _ = _make_db([
        [],  # documents_by_month
        [],  # recent_documents
    ])
    get_n, _ = _make_neo4j([])  # no entities
    with _mock.patch.object(tl_mod, "get_db", get_db), \
         _mock.patch.object(tl_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(tl_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(tl_mod.get_timeline(
            current_user={"id": 1, "username": "alice"},
        )))
    check("empty: ok", kind == "ok")
    check("empty: documents_by_month is []", body.documents_by_month == [])
    check("empty: recent_documents is []", body.recent_documents == [])
    check("empty: entity_timeline is []", body.entity_timeline == [])


def test_documents_by_month_groups_by_year_month():
    """strftime('%Y-%m', created_at) does the bucketing; the endpoint
    must order by month ascending so the front-end bar chart reads L→R
    chronologically."""
    from app.api import timeline as tl_mod
    import unittest.mock as _mock
    get_db, _ = _make_db([
        [
            {"month": "2025-10", "count": 3},
            {"month": "2025-11", "count": 7},
            {"month": "2025-12", "count": 2},
        ],
        [],  # recent_documents
    ])
    get_n, _ = _make_neo4j([])
    with _mock.patch.object(tl_mod, "get_db", get_db), \
         _mock.patch.object(tl_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(tl_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(tl_mod.get_timeline(
            current_user={"id": 1, "username": "alice"},
        )))
    check("by-month: ok", kind == "ok")
    check("by-month: 3 buckets", len(body.documents_by_month) == 3)
    check("by-month: 2025-10 has count 3",
          body.documents_by_month[0].month == "2025-10"
          and body.documents_by_month[0].count == 3)
    check("by-month: 2025-12 has count 2",
          body.documents_by_month[2].month == "2025-12"
          and body.documents_by_month[2].count == 2)


def test_recent_documents_returns_last_10_desc():
    """The endpoint asks SQLite for the most recent 10 — we assert the
    query uses ORDER BY created_at DESC LIMIT 10."""
    from app.api import timeline as tl_mod
    import unittest.mock as _mock
    rows = [
        {"id": f"doc-{i}", "title": f"Doc {i}", "original_filename": f"f{i}.pdf",
         "created_at": f"2025-12-{15 - i:02d} 10:00:00"}
        for i in range(1, 11)
    ]
    get_db, db = _make_db([
        [],  # documents_by_month
        rows,
    ])
    get_n, _ = _make_neo4j([])
    with _mock.patch.object(tl_mod, "get_db", get_db), \
         _mock.patch.object(tl_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(tl_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(tl_mod.get_timeline(
            current_user={"id": 1, "username": "alice"},
        )))
    check("recent: ok", kind == "ok")
    check("recent: returns all 10", len(body.recent_documents) == 10)
    check("recent: SQL uses ORDER BY created_at DESC LIMIT 10",
          "ORDER BY created_at DESC" in db.execute_calls[1][0]
          and "LIMIT 10" in db.execute_calls[1][0])


def test_entity_timeline_computes_first_seen_from_doc_dates():
    """The Neo4j side gives us (entity, [document_ids]) — we then look
    up each doc's created_at in SQLite, take the min, and that's the
    entity's first_seen date."""
    from app.api import timeline as tl_mod
    import unittest.mock as _mock

    # Neo4j returns two entities, each mentioning one or two docs.
    entities = [
        {"name": "硅基流动", "type": "Product",
         "chunk_ids": ["c1", "c2"], "document_ids": ["doc-a", "doc-b"],
         "mention_count": 2},
        {"name": "百炼", "type": "Product",
         "chunk_ids": ["c3"], "document_ids": ["doc-c"],
         "mention_count": 1},
    ]
    doc_meta_rows = [
        {"id": "doc-a", "title": "Alpha", "created_at": "2025-10-15 10:00:00"},
        {"id": "doc-b", "title": "Beta",  "created_at": "2025-11-20 09:00:00"},
        {"id": "doc-c", "title": "Gamma", "created_at": "2025-12-05 14:00:00"},
    ]
    get_db, _ = _make_db([
        [],  # documents_by_month
        [],  # recent_documents
        doc_meta_rows,  # the IN (...) lookup for entity doc_ids
    ])
    get_n, _ = _make_neo4j(entities)
    with _mock.patch.object(tl_mod, "get_db", get_db), \
         _mock.patch.object(tl_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(tl_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(tl_mod.get_timeline(
            current_user={"id": 1, "username": "alice"},
        )))
    check("entity: ok", kind == "ok")
    check("entity: 2 timeline items", len(body.entity_timeline) == 2)

    by_name = {e.name: e for e in body.entity_timeline}
    check("entity: 硅基流动 first_seen is 2025-10-15 (earliest of its 2 docs)",
          str(by_name["硅基流动"].first_seen) == "2025-10-15"
          and by_name["硅基流动"].first_seen_doc_id == "doc-a")
    check("entity: 硅基流动 doc_count is 2",
          by_name["硅基流动"].doc_count == 2)
    check("entity: 百炼 first_seen is 2025-12-05",
          str(by_name["百炼"].first_seen) == "2025-12-05")


def test_entity_timeline_sorted_newest_first():
    """The sort key is (has_date desc, -date_ord, -mention_count, name).
    The newest date comes first; ties go to higher mention_count."""
    from app.api import timeline as tl_mod
    import unittest.mock as _mock

    entities = [
        {"name": "A", "type": "T", "chunk_ids": ["c1"], "document_ids": ["d1"],
         "mention_count": 1},
        {"name": "B", "type": "T", "chunk_ids": ["c2"], "document_ids": ["d2"],
         "mention_count": 5},  # most-mentioned, but OLDER date
        {"name": "C", "type": "T", "chunk_ids": ["c3"], "document_ids": ["d3"],
         "mention_count": 1},
    ]
    doc_rows = [
        {"id": "d1", "title": "Doc1", "created_at": "2025-11-15 10:00:00"},
        {"id": "d2", "title": "Doc2", "created_at": "2025-10-01 10:00:00"},
        {"id": "d3", "title": "Doc3", "created_at": "2025-12-20 10:00:00"},
    ]
    get_db, _ = _make_db([[], [], doc_rows])
    get_n, _ = _make_neo4j(entities)
    with _mock.patch.object(tl_mod, "get_db", get_db), \
         _mock.patch.object(tl_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(tl_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(tl_mod.get_timeline(
            current_user={"id": 1, "username": "alice"},
        )))
    names = [e.name for e in body.entity_timeline]
    check("entity sort: C (newest) is first", names[0] == "C")
    check("entity sort: A is second", names[1] == "A")
    check("entity sort: B (oldest) is last", names[2] == "B")


def test_entity_with_no_live_documents_still_appears():
    """If every doc that mentioned an entity has been deleted, the
    entity is still listed (Neo4j still has the node) but with
    first_seen=None and doc_count=0. mention_count is preserved so the
    user can see the entity is still "around"."""
    from app.api import timeline as tl_mod
    import unittest.mock as _mock

    entities = [
        {"name": "Ghost", "type": "Concept",
         "chunk_ids": ["c1"], "document_ids": ["doc-deleted"],
         "mention_count": 7},
    ]
    # The IN (...) lookup returns NO rows because doc-deleted is gone.
    get_db, _ = _make_db([[], [], []])
    get_n, _ = _make_neo4j(entities)
    with _mock.patch.object(tl_mod, "get_db", get_db), \
         _mock.patch.object(tl_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(tl_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(tl_mod.get_timeline(
            current_user={"id": 1, "username": "alice"},
        )))
    check("ghost: ok", kind == "ok")
    check("ghost: entity still listed", len(body.entity_timeline) == 1)
    ghost = body.entity_timeline[0]
    check("ghost: first_seen is None", ghost.first_seen is None)
    check("ghost: doc_count is 0", ghost.doc_count == 0)
    check("ghost: mention_count preserved", ghost.mention_count == 7)


def test_user_isolation_in_neo4j_call():
    """The endpoint must pass the calling user's id to the Neo4j method
    so user A's timeline never includes user B's entities."""
    from app.api import timeline as tl_mod
    import unittest.mock as _mock
    get_db, _ = _make_db([[], [], []])
    get_n, n = _make_neo4j([])
    with _mock.patch.object(tl_mod, "get_db", get_db), \
         _mock.patch.object(tl_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(tl_mod, "get_current_user", _stub_user(42)):
        asyncio.run(tl_mod.get_timeline(
            current_user={"id": 42, "username": "carol"},
        ))
    check("isolation: Neo4j was called with the right user_id",
          n.calls and n.calls[0][0] == 42)


def test_user_isolation_in_sqlite_queries():
    """The SQLite queries also bind to user_id. We assert on the FIRST
    parameter of each."""
    from app.api import timeline as tl_mod
    import unittest.mock as _mock
    get_db, db = _make_db([[], [], []])
    get_n, _ = _make_neo4j([])
    with _mock.patch.object(tl_mod, "get_db", get_db), \
         _mock.patch.object(tl_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(tl_mod, "get_current_user", _stub_user(7)):
        asyncio.run(tl_mod.get_timeline(
            current_user={"id": 7, "username": "bob"},
        ))
    # All SQLite calls should have user_id=7 as the first positional param.
    for i, (sql, params) in enumerate(db.execute_calls):
        check(f"isolation: SQLite call #{i} binds to user_id=7",
              len(params) >= 1 and params[0] == 7)


# =========================================================================
# Driver
# =========================================================================

# =========================================================================

# Regression guard: Cypher used by get_user_entities_with_mentions must
# traverse (:Document)-[:CONTAINS]->(:Chunk) rather than reference
# `c.document_id` as a property. The schema is edge-based; an earlier
# revision of this query used the property and silently returned empty
# document_ids, which broke the timeline endpoint's first_seen
# computation. This guard locks the edge-based pattern in.
# =========================================================================

def test_timeline_cypher_uses_contains_edge():
    from app.services.neo4j_client import Neo4jClient

    client = Neo4jClient.__new__(Neo4jClient)

    class _Cursor:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def __aiter__(self): return self
        async def __anext__(self):
            raise StopAsyncIteration

    captured_cypher = {}

    class _Session:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def run(self, cypher, **params):
            captured_cypher["text"] = str(cypher)
            return _Cursor()

    class _Driver:
        def session(self): return _Session()

    client._driver = _Driver()
    asyncio.run(client.get_user_entities_with_mentions(user_id=1, limit=5))
    text = captured_cypher["text"]
    check("timeline cypher: traverses (:Document)-[:CONTAINS]->(Chunk)",
          ":Document" in text and "[:CONTAINS]" in text and ":Chunk" in text)
    check("timeline cypher: does NOT reference c.document_id as a property",
          "c.document_id" not in text)
    # MENTIONS direction is Chunk → Entity (created by
    # link_chunk_to_entity). The reverse direction silently matches
    # nothing — which is exactly the bug we just fixed (mention_count
    # was 0 for every entity in real data). Locking this in prevents
    # another "right but empty" Cypher regression.
    check("timeline cypher: MENTIONS direction is Chunk → Entity",
          "(c:Chunk" in text and "[:MENTIONS]->(e" in text)
    check("timeline cypher: does NOT use the wrong Entity → Chunk direction",
          "(e:Entity)-[:MENTIONS]->(c" not in text
          and "(e)-[:MENTIONS]->(c" not in text)

ALL_TESTS = [
    test_to_date_from_iso_string,
    test_to_date_from_sqlite_timestamp,
    test_to_date_from_iso_timestamp,
    test_to_date_handles_invalid_and_empty,
    test_first_seen_picks_earliest,
    test_first_seen_empty_input,
    test_empty_user_returns_empty_lists,
    test_documents_by_month_groups_by_year_month,
    test_recent_documents_returns_last_10_desc,
    test_entity_timeline_computes_first_seen_from_doc_dates,
    test_entity_timeline_sorted_newest_first,
    test_entity_with_no_live_documents_still_appears,
    test_user_isolation_in_neo4j_call,
    test_user_isolation_in_sqlite_queries,
    test_timeline_cypher_uses_contains_edge,
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
