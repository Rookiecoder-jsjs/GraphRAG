"""Tests for #13 dashboard summary endpoint.

Standalone runner:
    cd backend
    ../.venv/Scripts/python.exe tests/test_dashboard.py

Coverage:
  1. Empty user — all counts are 0, lists are empty
  2. Hero stats — documents/chunks/entities/relations/conversations/messages/tags
  3. Recent activity — merge uploads + messages, sorted desc, capped at 10
  4. Top entities — first 10 by mention count (re-uses the existing
     get_user_entities_with_mentions helper)
  5. Top tags — sorted by count desc
  6. Growth — fills 6 contiguous months, missing months get count 0
  7. User isolation — all queries bind to the calling user
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from datetime import datetime, timedelta

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

    async def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
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
    def __init__(self, *, entity_count=0, relation_count=0, top_entities=None):
        self._entity_count = entity_count
        self._relation_count = relation_count
        self._top_entities = top_entities or []
        self.calls: list = []

    async def count_user_entities(self, user_id):
        self.calls.append(("count_entities", user_id))
        return self._entity_count

    async def count_user_relations(self, user_id):
        self.calls.append(("count_relations", user_id))
        return self._relation_count

    async def get_user_entities_with_mentions(self, user_id, limit=200):
        self.calls.append(("top_entities", user_id, limit))
        return self._top_entities


def _make_neo4j(**kw):
    n = _FakeNeo4j(**kw)
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


def test_empty_user_returns_zeros_and_empty_lists():
    """A brand-new user with nothing should see all-zero stats and no
    activity / entities / tags. The 6-bucket growth list is always
    returned (6 months including the current one)."""
    from app.api import dashboard as dash_mod
    import unittest.mock as _mock
    get_db, _ = _make_db([
        [{"n": 0}],  # documents
        [{"n": 0}],  # chunks
        [{"n": 0}],  # conversations
        [{"n": 0}],  # messages
        [{"n": 0}],  # tags
        [],          # recent documents
        [],          # recent messages
        [],          # top tags
        [],          # growth (no rows in the last 6 months)
    ])
    get_n, _ = _make_neo4j(entity_count=0, relation_count=0, top_entities=[])
    with _mock.patch.object(dash_mod, "get_db", get_db), \
         _mock.patch.object(dash_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(dash_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(dash_mod.get_dashboard_summary(
            current_user={"id": 1, "username": "alice"},
        )))
    check("empty: ok", kind == "ok")
    check("empty: documents=0", body.stats.documents == 0)
    check("empty: chunks=0", body.stats.chunks == 0)
    check("empty: entities=0", body.stats.entities == 0)
    check("empty: relations=0", body.stats.relations == 0)
    check("empty: conversations=0", body.stats.conversations == 0)
    check("empty: messages=0", body.stats.messages == 0)
    check("empty: tags=0", body.stats.tags == 0)
    check("empty: recent_activity is []", body.recent_activity == [])
    check("empty: top_entities is []", body.top_entities == [])
    check("empty: top_tags is []", body.top_tags == [])
    check("empty: growth has 6 buckets (zero-filled)", len(body.growth) == 6)
    check("empty: every growth bucket has count=0",
          all(b.count == 0 for b in body.growth))


def test_hero_stats_pull_through():
    """The 5 SQLite counts and 2 Neo4j counts make it into the response."""
    from app.api import dashboard as dash_mod
    import unittest.mock as _mock
    get_db, _ = _make_db([
        [{"n": 12}],  # documents
        [{"n": 134}],  # chunks
        [{"n": 3}],   # conversations
        [{"n": 47}],  # messages
        [{"n": 6}],   # distinct tags
        [],            # recent docs
        [],            # recent msgs
        [],            # top tags
        [],            # growth
    ])
    get_n, _ = _make_neo4j(entity_count=58, relation_count=121, top_entities=[])
    with _mock.patch.object(dash_mod, "get_db", get_db), \
         _mock.patch.object(dash_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(dash_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(dash_mod.get_dashboard_summary(
            current_user={"id": 1, "username": "alice"},
        )))
    check("stats: ok", kind == "ok")
    check("stats: documents=12", body.stats.documents == 12)
    check("stats: chunks=134", body.stats.chunks == 134)
    check("stats: entities=58", body.stats.entities == 58)
    check("stats: relations=121", body.stats.relations == 121)
    check("stats: conversations=3", body.stats.conversations == 3)
    check("stats: messages=47", body.stats.messages == 47)
    check("stats: tags=6", body.stats.tags == 6)


def test_recent_activity_merges_and_sorts():
    """Activity = recent docs + recent messages, sorted desc by created_at,
    capped at 10. We supply 4 docs and 4 msgs to make sure both kinds
    appear and the ordering is right."""
    from app.api import dashboard as dash_mod
    import unittest.mock as _mock

    now = datetime(2026, 6, 5, 17, 0, 0)
    def ago(minutes):
        return (now - timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")

    doc_rows = [
        {"id": "doc-1", "title": "D1", "original_filename": "d1.pdf",
         "created_at": ago(60)},
        {"id": "doc-2", "title": "D2", "original_filename": "d2.pdf",
         "created_at": ago(5)},
        {"id": "doc-3", "title": "D3", "original_filename": "d3.pdf",
         "created_at": ago(30)},
        {"id": "doc-4", "title": "D4", "original_filename": "d4.pdf",
         "created_at": ago(120)},
    ]
    msg_rows = [
        {"msg_id": 101, "conversation_id": "c-1", "role": "user",
         "content": "hello world", "created_at": ago(2),  "conv_title": "Chat 1"},
        {"msg_id": 102, "conversation_id": "c-1", "role": "assistant",
         "content": "hi there!", "created_at": ago(1),  "conv_title": "Chat 1"},
        {"msg_id": 103, "conversation_id": "c-2", "role": "user",
         "content": "what is X?", "created_at": ago(15), "conv_title": "Chat 2"},
        {"msg_id": 104, "conversation_id": "c-2", "role": "assistant",
         "content": "X is...",    "created_at": ago(10), "conv_title": "Chat 2"},
    ]
    get_db, _ = _make_db([
        [{"n": 4}],   # documents count
        [{"n": 0}],   # chunks
        [{"n": 2}],   # conversations
        [{"n": 4}],   # messages
        [{"n": 0}],   # tags
        doc_rows,      # recent docs
        msg_rows,      # recent msgs
        [],            # top tags
        [],            # growth
    ])
    get_n, _ = _make_neo4j()
    with _mock.patch.object(dash_mod, "get_db", get_db), \
         _mock.patch.object(dash_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(dash_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(dash_mod.get_dashboard_summary(
            current_user={"id": 1, "username": "alice"},
        )))
    check("activity: ok", kind == "ok")
    check("activity: 8 items merged (4 docs + 4 msgs)", len(body.recent_activity) == 8)
    check("activity: newest item is msg 102 (1m ago)",
          body.recent_activity[0].kind == "message"
          and body.recent_activity[0].id == "102")
    check("activity: 2nd is msg 101 (2m ago)",
          body.recent_activity[1].kind == "message"
          and body.recent_activity[1].id == "101")
    check("activity: 3rd is doc-2 (5m ago)",
          body.recent_activity[2].kind == "document"
          and body.recent_activity[2].id == "doc-2")
    docs_in_activity = [a for a in body.recent_activity if a.kind == "document"]
    msgs_in_activity = [a for a in body.recent_activity if a.kind == "message"]
    check("activity: docs have no conversation_id",
          all(a.conversation_id is None for a in docs_in_activity))
    check("activity: msgs have conversation_id set",
          all(a.conversation_id is not None for a in msgs_in_activity))


def test_recent_activity_truncates_long_message_bodies():
    """Message bodies are clipped to 80 chars + ellipsis for the activity
    feed. Otherwise a 10k-token chat would dominate the card."""
    from app.api import dashboard as dash_mod
    import unittest.mock as _mock
    long_text = "x" * 200
    msg_rows = [
        {"msg_id": 1, "conversation_id": "c-1", "role": "user",
         "content": long_text, "created_at": "2026-06-05 17:00:00",
         "conv_title": "Chat 1"},
    ]
    get_db, _ = _make_db([
        [{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],
        [], msg_rows, [], [],
    ])
    get_n, _ = _make_neo4j()
    with _mock.patch.object(dash_mod, "get_db", get_db), \
         _mock.patch.object(dash_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(dash_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(dash_mod.get_dashboard_summary(
            current_user={"id": 1, "username": "alice"},
        )))
    msg = body.recent_activity[0]
    check("truncate: title is exactly 80 chars (77 + '...')",
          len(msg.title) == 80 and msg.title.endswith("..."))
    check("truncate: title contains only 'x' and ellipsis",
          set(msg.title) == {"x", "."})


def test_top_entities_passed_through_with_doc_count():
    """The endpoint re-uses `get_user_entities_with_mentions` and
    surfaces the first 10. `doc_count` is the number of distinct
    document_ids in that list."""
    from app.api import dashboard as dash_mod
    import unittest.mock as _mock
    entities = [
        {"name": "硅基流动", "type": "Product", "chunk_ids": ["c1","c2","c3"],
         "document_ids": ["doc-a", "doc-b"], "mention_count": 3},
        {"name": "百炼", "type": "Product", "chunk_ids": ["c4"],
         "document_ids": ["doc-c"], "mention_count": 1},
    ]
    get_db, _ = _make_db([
        [{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],
        [], [], [], [],
    ])
    get_n, _ = _make_neo4j(top_entities=entities)
    with _mock.patch.object(dash_mod, "get_db", get_db), \
         _mock.patch.object(dash_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(dash_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(dash_mod.get_dashboard_summary(
            current_user={"id": 1, "username": "alice"},
        )))
    check("top-entities: ok", kind == "ok")
    check("top-entities: 2 items", len(body.top_entities) == 2)
    check("top-entities: 硅基流动 doc_count=2, mention_count=3",
          body.top_entities[0].doc_count == 2
          and body.top_entities[0].mention_count == 3)
    check("top-entities: 百炼 doc_count=1, mention_count=1",
          body.top_entities[1].doc_count == 1
          and body.top_entities[1].mention_count == 1)


def test_top_tags_sorted_by_count_desc():
    from app.api import dashboard as dash_mod
    import unittest.mock as _mock
    tag_rows = [
        {"tag": "research", "count": 5},
        {"tag": "draft", "count": 3},
        {"tag": "2024q3", "count": 2},
    ]
    get_db, _ = _make_db([
        [{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],
        [], [], tag_rows, [],
    ])
    get_n, _ = _make_neo4j()
    with _mock.patch.object(dash_mod, "get_db", get_db), \
         _mock.patch.object(dash_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(dash_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(dash_mod.get_dashboard_summary(
            current_user={"id": 1, "username": "alice"},
        )))
    check("top-tags: ok", kind == "ok")
    check("top-tags: 3 items in count desc order",
          [t.tag for t in body.top_tags] == ["research", "draft", "2024q3"])
    check("top-tags: counts preserved",
          [t.count for t in body.top_tags] == [5, 3, 2])


def test_growth_fills_missing_months_with_zero():
    """If the user only uploaded in 2 of the last 6 months, the response
    still has 6 contiguous buckets — the missing months are 0."""
    from app.api import dashboard as dash_mod
    import unittest.mock as _mock
    now = datetime.now()
    two_back = now.month - 2
    this = now.month
    growth_rows = [
        {"month": f"{now.year:04d}-{this:02d}", "count": 4},
        {"month": f"{now.year:04d}-{two_back:02d}", "count": 7},
    ]
    get_db, _ = _make_db([
        [{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],
        [], [], [], growth_rows,
    ])
    get_n, _ = _make_neo4j()
    with _mock.patch.object(dash_mod, "get_db", get_db), \
         _mock.patch.object(dash_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(dash_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(dash_mod.get_dashboard_summary(
            current_user={"id": 1, "username": "alice"},
        )))
    check("growth: 6 buckets", len(body.growth) == 6)
    by_month = {b.month: b.count for b in body.growth}
    check("growth: this-month bucket matches",
          by_month[f"{now.year:04d}-{this:02d}"] == 4)
    check("growth: 2-months-ago bucket matches",
          by_month[f"{now.year:04d}-{two_back:02d}"] == 7)
    total = sum(b.count for b in body.growth)
    check("growth: total counts = sum of provided rows (no double-counting)",
          total == 11)


def test_growth_is_chronologically_ascending():
    """The buckets must go oldest -> newest, so the front-end bar
    chart's x-axis is left-to-right time."""
    from app.api import dashboard as dash_mod
    import unittest.mock as _mock
    get_db, _ = _make_db([
        [{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],
        [], [], [], [],
    ])
    get_n, _ = _make_neo4j()
    with _mock.patch.object(dash_mod, "get_db", get_db), \
         _mock.patch.object(dash_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(dash_mod, "get_current_user", _stub_user(1)):
        kind, _, body = asyncio.run(_call(dash_mod.get_dashboard_summary(
            current_user={"id": 1, "username": "alice"},
        )))
    months = [b.month for b in body.growth]
    check("growth order: months are ascending strings",
          months == sorted(months))


def test_user_isolation_all_sqlite_queries_bound_to_user():
    """Every SQLite call has the calling user's id as the first param.
    This guards against the most common multi-tenant leak: forgetting
    a WHERE clause on a count or list query."""
    from app.api import dashboard as dash_mod
    import unittest.mock as _mock
    get_db, db = _make_db([
        [{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],
        [], [], [], [],
    ])
    get_n, _ = _make_neo4j()
    with _mock.patch.object(dash_mod, "get_db", get_db), \
         _mock.patch.object(dash_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(dash_mod, "get_current_user", _stub_user(99)):
        asyncio.run(dash_mod.get_dashboard_summary(
            current_user={"id": 99, "username": "carol"},
        ))
    for i, (sql, params) in enumerate(db.execute_calls):
        check(f"isolation: SQLite call #{i} binds user_id=99 as first param",
              len(params) >= 1 and params[0] == 99)


def test_user_isolation_neo4j_calls_bound_to_user():
    """The two Neo4j count calls + the top-entities call must all be
    invoked with the right user_id."""
    from app.api import dashboard as dash_mod
    import unittest.mock as _mock
    get_db, _ = _make_db([
        [{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],[{"n": 0}],
        [], [], [], [],
    ])
    get_n, n = _make_neo4j()
    with _mock.patch.object(dash_mod, "get_db", get_db), \
         _mock.patch.object(dash_mod, "get_neo4j_client", get_n), \
         _mock.patch.object(dash_mod, "get_current_user", _stub_user(42)):
        asyncio.run(dash_mod.get_dashboard_summary(
            current_user={"id": 42, "username": "bob"},
        ))
    user_ids_used = [c[1] for c in n.calls]
    check("isolation: all 3 Neo4j calls used user_id=42",
          all(uid == 42 for uid in user_ids_used)
          and len(user_ids_used) == 3)


# =========================================================================
# Driver
# =========================================================================

ALL_TESTS = [
    test_empty_user_returns_zeros_and_empty_lists,
    test_hero_stats_pull_through,
    test_recent_activity_merges_and_sorts,
    test_recent_activity_truncates_long_message_bodies,
    test_top_entities_passed_through_with_doc_count,
    test_top_tags_sorted_by_count_desc,
    test_growth_fills_missing_months_with_zero,
    test_growth_is_chronologically_ascending,
    test_user_isolation_all_sqlite_queries_bound_to_user,
    test_user_isolation_neo4j_calls_bound_to_user,
]


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks for #13 dashboard...")
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
