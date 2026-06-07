"""Tests for #11 document tags.

Standalone runner:
    cd backend
    ../.venv/Scripts/python.exe tests/test_tags.py

Coverage:
  1. normalize_tag() — pure-function behaviour (lowercase, trim,
     strip leading '#', blank input -> None)
  2. list/add/remove tags per document — including 404 on missing
     document, idempotency on duplicate add, 200 on no-op remove
  3. list_user_tags — distinct tag list with counts, sorted by count desc
  4. list_documents — ?tag= filter and tags: [] on the response
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.models.document import TagCreate, TagResponse  # noqa: E402


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

def test_normalize_tag_lowercases():
    from app.api.documents import normalize_tag
    check("normalize: 'Research' -> 'research'", normalize_tag("Research") == "research")
    check("normalize: 'RESEARCH' -> 'research'", normalize_tag("RESEARCH") == "research")


def test_normalize_tag_trims_whitespace():
    from app.api.documents import normalize_tag
    check("normalize: '  spaced  ' -> 'spaced'",
          normalize_tag("  spaced  ") == "spaced")
    check("normalize: '\\n\\tresearch\\n' -> 'research'",
          normalize_tag("\n\tresearch\n") == "research")


def test_normalize_tag_strips_leading_hash():
    from app.api.documents import normalize_tag
    check("normalize: '#research' -> 'research'",
          normalize_tag("#research") == "research")
    check("normalize: '### research' -> 'research'",
          normalize_tag("### research") == "research")
    check("normalize: '# spaced' -> 'spaced'",
          normalize_tag("# spaced") == "spaced")
    # Hash mid-string is preserved (it's only a *leading* marker).
    check("normalize: 'foo#bar' kept as 'foo#bar'",
          normalize_tag("foo#bar") == "foo#bar")


def test_normalize_tag_blank_returns_none():
    from app.api.documents import normalize_tag
    check("normalize: '' -> None", normalize_tag("") is None)
    check("normalize: '   ' -> None", normalize_tag("   ") is None)
    check("normalize: '###' -> None", normalize_tag("###") is None)
    check("normalize: '# # #' -> None", normalize_tag("# # #") is None)
    check("normalize: None -> None", normalize_tag(None) is None)


def test_normalize_tag_handles_unicode():
    from app.api.documents import normalize_tag
    check("normalize: '中文研究' lowercased",
          normalize_tag("中文研究") == "中文研究")
    check("normalize: '中文' -> '中文'",
          normalize_tag("中文") == "中文")


# =========================================================================
# Fake DB driver
# =========================================================================
#
# The real `get_db` is an async-context-manager that returns a connection
# whose `.execute()` returns an async-context-manager wrapping a cursor
# that supports `await cursor.fetchall()`. We model that surface area
# exactly. The script's `scripted` list is consumed one entry per
# `.execute()` call — each entry being the list of rows that
# `fetchall()` should yield for that call.

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
    """Mimics aiosqlite's `Connection.execute()` return value, which is
    BOTH awaitable and an async context manager — production code uses
    whichever is convenient (`await db.execute(...)` for writes whose
    cursor is discarded, `async with db.execute(...) as cursor:` for
    reads)."""
    def __init__(self, cursor):
        self._cursor = cursor

    def __await__(self):
        async def _resolve():
            return self._cursor
        return _resolve().__await__()

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *a):
        return False


class _FakeDb:
    """A minimal aiosqlite stand-in. `scripted[i]` is consumed by the
    i-th call to `execute()`. We also keep a record of all execute()
    calls + their params so tests can assert what was written."""
    def __init__(self, scripted_rows_per_execute):
        self._scripted = [list(r) for r in scripted_rows_per_execute]
        self.execute_calls: list = []  # (sql, params)

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


def _make_db(scripted_rows_per_execute):
    """Return a callable suitable for `patch.object(module, 'get_db', ...)`."""
    db = _FakeDb(scripted_rows_per_execute)
    def _get_db():
        return db
    return _get_db, db


# =========================================================================
# Per-document tag endpoints
# =========================================================================

USERS = {1: "alice", 2: "bob"}


def _stub_current_user(user_id: int):
    """Build a `Depends(get_current_user)` substitute that returns
    the supplied user."""
    from app.api.auth import get_current_user
    async def _dep():
        return {"id": user_id, "username": USERS[user_id]}
    return _dep


async def _call(coro):
    """Drive a coroutine and return (status, kind, payload)."""
    try:
        result = await coro
        return ("ok", None, result)
    except Exception as e:
        return ("err", type(e).__name__, getattr(e, "detail", str(e)))


def test_list_tags_returns_sorted_list():
    """When the DB has tags for the doc, return them in tag-alphabetical
    order (which is what the SQL query asks for)."""
    from app.api import documents as docs_mod
    import unittest.mock as _mock
    get_db, db = _make_db([
        [{"1": 1}],
        [{"tag": "research"}, {"tag": "draft"}],
    ])
    with _mock.patch.object(docs_mod, "get_db", get_db), \
         _mock.patch.object(docs_mod, "get_current_user", _stub_current_user(1)):
        kind, _, body = asyncio.run(_call(docs_mod.list_document_tags(
            doc_id="doc-aaa", current_user={"id": 1, "username": "alice"},
        )))
    check("list_tags: returns ok", kind == "ok")
    check("list_tags: result is a list", isinstance(body, list))
    check("list_tags: result matches DB rows", body == ["research", "draft"])


def test_list_tags_404_on_missing_doc():
    """The ownership check is `SELECT 1 FROM documents WHERE id=? AND user_id=?`.
    If it returns no row, the doc is missing or owned by another user — 404."""
    from app.api import documents as docs_mod
    import unittest.mock as _mock
    get_db, _ = _make_db([
        [],
    ])
    with _mock.patch.object(docs_mod, "get_db", get_db), \
         _mock.patch.object(docs_mod, "get_current_user", _stub_current_user(1)):
        kind, status, body = asyncio.run(_call(docs_mod.list_document_tags(
            doc_id="missing-doc", current_user={"id": 1, "username": "alice"},
        )))
    check("list_tags: 404 when doc missing",
          kind == "err" and status == "HTTPException" and "Document not found" in body)


def test_add_tag_normalises_and_inserts():
    """The endpoint lower-cases + strips '#' before storing, and the
    response is the full updated tag list (re-fetched)."""
    from app.api import documents as docs_mod
    import unittest.mock as _mock
    get_db, db = _make_db([
        [{"1": 1}],
        [],
        [{"tag": "research"}],
    ])
    with _mock.patch.object(docs_mod, "get_db", get_db), \
         _mock.patch.object(docs_mod, "get_current_user", _stub_current_user(1)):
        kind, _, body = asyncio.run(_call(docs_mod.add_document_tag(
            doc_id="doc-aaa", body=TagCreate(tag="  #Research  "),
            current_user={"id": 1, "username": "alice"},
        )))
    check("add_tag: ok", kind == "ok")
    check("add_tag: returns sorted updated list", body == ["research"])
    insert_calls = [c for c in db.execute_calls if "INSERT" in c[0]]
    check("add_tag: exactly one INSERT call", len(insert_calls) == 1)
    check("add_tag: stored normalised 'research'",
          insert_calls[0][1] == ("doc-aaa", 1, "research"))


def test_add_tag_blank_returns_400():
    """Sending whitespace or just '#' as the tag body must be rejected,
    not silently stored as empty string. Empty string is caught by
    Pydantic (min_length=1) and tested separately in test_tag_create_validation."""
    from app.api import documents as docs_mod
    import unittest.mock as _mock
    with _mock.patch.object(docs_mod, "get_current_user", _stub_current_user(1)):
        for bad in ["   ", "###", "# # #"]:
            kind, status, _ = asyncio.run(_call(docs_mod.add_document_tag(
                doc_id="doc-aaa", body=TagCreate(tag=bad),
                current_user={"id": 1, "username": "alice"},
            )))
            check(f"add_tag: rejects blank tag {bad!r}",
                  kind == "err" and status == "HTTPException")


def test_add_tag_404_on_missing_doc():
    """Ownership check fails before any INSERT runs."""
    from app.api import documents as docs_mod
    import unittest.mock as _mock
    get_db, db = _make_db([[]])
    with _mock.patch.object(docs_mod, "get_db", get_db), \
         _mock.patch.object(docs_mod, "get_current_user", _stub_current_user(1)):
        kind, status, _ = asyncio.run(_call(docs_mod.add_document_tag(
            doc_id="missing", body=TagCreate(tag="x"),
            current_user={"id": 1, "username": "alice"},
        )))
    check("add_tag: 404 on missing doc", kind == "err" and status == "HTTPException")
    check("add_tag: no INSERT ran on missing doc",
          not any("INSERT" in c[0] for c in db.execute_calls))


def test_remove_tag_normalises_path_param():
    """The URL path may contain whatever the client sent. We normalise
    the path tag against the same rules as the add endpoint so the
    caller can pass 'Research' or '#research' interchangeably."""
    from app.api import documents as docs_mod
    import unittest.mock as _mock
    get_db, db = _make_db([
        [{"1": 1}],
        [],
        [],
    ])
    with _mock.patch.object(docs_mod, "get_db", get_db), \
         _mock.patch.object(docs_mod, "get_current_user", _stub_current_user(1)):
        kind, _, body = asyncio.run(_call(docs_mod.remove_document_tag(
            doc_id="doc-aaa", tag="  #Research  ",
            current_user={"id": 1, "username": "alice"},
        )))
    check("remove_tag: ok", kind == "ok")
    check("remove_tag: returns empty updated list", body == [])
    delete_calls = [c for c in db.execute_calls if "DELETE" in c[0]]
    check("remove_tag: DELETE used normalised 'research'",
          delete_calls[0][1] == ("doc-aaa", 1, "research"))


def test_remove_tag_blank_returns_400():
    """Same validation as add — never persist empty tags even on the
    way out (avoids a class of bug where the path encodes garbage)."""
    from app.api import documents as docs_mod
    import unittest.mock as _mock
    with _mock.patch.object(docs_mod, "get_current_user", _stub_current_user(1)):
        for bad in ["", "   ", "###"]:
            kind, status, _ = asyncio.run(_call(docs_mod.remove_document_tag(
                doc_id="doc-aaa", tag=bad,
                current_user={"id": 1, "username": "alice"},
            )))
            check(f"remove_tag: rejects blank path tag {bad!r}",
                  kind == "err" and status == "HTTPException")


def test_remove_tag_is_idempotent_on_missing_tag():
    """Removing a tag that doesn't exist on the doc is NOT an error —
    it's a no-op. This matches REST convention for DELETE."""
    from app.api import documents as docs_mod
    import unittest.mock as _mock
    get_db, _ = _make_db([
        [{"1": 1}],
        [],
        [{"tag": "other"}],
    ])
    with _mock.patch.object(docs_mod, "get_db", get_db), \
         _mock.patch.object(docs_mod, "get_current_user", _stub_current_user(1)):
        kind, _, body = asyncio.run(_call(docs_mod.remove_document_tag(
            doc_id="doc-aaa", tag="ghost-tag",
            current_user={"id": 1, "username": "alice"},
        )))
    check("remove_tag: 200 on missing tag (idempotent)", kind == "ok")
    check("remove_tag: returns the surviving tags", body == ["other"])


# =========================================================================
# Global user-tag list with counts
# =========================================================================

def test_list_user_tags_groups_and_counts():
    """The endpoint groups tags and returns count desc / tag asc."""
    from app.api import tags as tags_mod
    import unittest.mock as _mock
    get_db, _ = _make_db([
        [
            {"tag": "research", "count": 3},
            {"tag": "draft", "count": 2},
            {"tag": "important", "count": 1},
        ],
    ])
    with _mock.patch.object(tags_mod, "get_db", get_db), \
         _mock.patch.object(tags_mod, "get_current_user", _stub_current_user(1)):
        kind, _, body = asyncio.run(_call(tags_mod.list_user_tags(
            current_user={"id": 1, "username": "alice"},
        )))
    check("list_user_tags: ok", kind == "ok")
    check("list_user_tags: returns 3 items", len(body) == 3)
    check("list_user_tags: each is TagResponse",
          all(isinstance(t, TagResponse) for t in body))
    check("list_user_tags: top is most-used",
          body[0].tag == "research" and body[0].count == 3)
    check("list_user_tags: counts are integers",
          all(isinstance(t.count, int) for t in body))


def test_list_user_tags_user_isolation():
    """The query must always include `user_id = ?`. We assert the
    params — and, more importantly, that the response only contains
    this user's tags (modelled by what the fake DB returns)."""
    from app.api import tags as tags_mod
    import unittest.mock as _mock
    get_db, db = _make_db([
        [{"tag": "alice-only", "count": 1}],
    ])
    with _mock.patch.object(tags_mod, "get_db", get_db), \
         _mock.patch.object(tags_mod, "get_current_user", _stub_current_user(1)):
        asyncio.run(tags_mod.list_user_tags(
            current_user={"id": 1, "username": "alice"},
        ))
    check("list_user_tags: query is filtered by user_id",
          db.execute_calls[0][1][0] == 1)
    check("list_user_tags: query selects explicit columns (not SELECT *)",
          "SELECT *" not in db.execute_calls[0][0])


def test_list_user_tags_with_q_substring_filter():
    """The optional `?q=foo` query narrows the list to tags whose name
    contains the substring (case-insensitive)."""
    from app.api import tags as tags_mod
    import unittest.mock as _mock
    get_db, db = _make_db([
        [{"tag": "research", "count": 2}, {"tag": "research-notes", "count": 1}],
    ])
    with _mock.patch.object(tags_mod, "get_db", get_db), \
         _mock.patch.object(tags_mod, "get_current_user", _stub_current_user(1)):
        kind, _, body = asyncio.run(_call(tags_mod.list_user_tags(
            current_user={"id": 1, "username": "alice"}, q="research",
        )))
    check("list_user_tags with q: ok", kind == "ok")
    check("list_user_tags with q: returns 2 matches", len(body) == 2)
    sql, params = db.execute_calls[0]
    check("list_user_tags with q: uses LIKE in SQL", "LIKE" in sql)
    check("list_user_tags with q: param is lowercased and wrapped in %",
          params == (1, "%research%"))


# =========================================================================
# list_documents — ?tag= filter and tags: [] on the response
# =========================================================================

def test_list_documents_includes_tags_in_response():
    """Each document in the list must carry a `tags: [...]` array
    (possibly empty) — built from a single follow-up SELECT to avoid
    N+1."""
    from app.api import documents as docs_mod
    import unittest.mock as _mock
    get_db, db = _make_db([
        [
            {"id": "doc-aaa", "title": "Alpha", "original_filename": "a.pdf",
             "file_type": "pdf", "created_at": "2026-06-01 00:00:00"},
            {"id": "doc-bbb", "title": "Beta", "original_filename": "b.pdf",
             "file_type": "pdf", "created_at": "2026-06-02 00:00:00"},
        ],
        [
            {"document_id": "doc-aaa", "tag": "research"},
            {"document_id": "doc-aaa", "tag": "draft"},
            {"document_id": "doc-bbb", "tag": "draft"},
        ],
    ])
    with _mock.patch.object(docs_mod, "get_db", get_db), \
         _mock.patch.object(docs_mod, "get_current_user", _stub_current_user(1)):
        kind, _, body = asyncio.run(_call(docs_mod.list_documents(
            current_user={"id": 1, "username": "alice"},
        )))
    check("list_documents: ok", kind == "ok")
    check("list_documents: returns 2 docs", len(body) == 2)
    by_id = {d["id"]: d for d in body}
    check("list_documents: doc-aaa has both tags",
          sorted(by_id["doc-aaa"]["tags"]) == ["draft", "research"])
    check("list_documents: doc-bbb has the one tag",
          by_id["doc-bbb"]["tags"] == ["draft"])


def test_list_documents_with_no_tags_returns_empty_list():
    """If a doc has zero tags, its tags field should be an empty list,
    not None and not missing."""
    from app.api import documents as docs_mod
    import unittest.mock as _mock
    get_db, db = _make_db([
        [{"id": "doc-aaa", "title": "Alpha", "original_filename": "a.pdf",
          "file_type": "pdf", "created_at": "2026-06-01 00:00:00"}],
        [],
    ])
    with _mock.patch.object(docs_mod, "get_db", get_db), \
         _mock.patch.object(docs_mod, "get_current_user", _stub_current_user(1)):
        kind, _, body = asyncio.run(_call(docs_mod.list_documents(
            current_user={"id": 1, "username": "alice"},
        )))
    check("list_documents (no tags): ok", kind == "ok")
    check("list_documents (no tags): tags field is []",
          body[0]["tags"] == [])


def test_list_documents_tag_filter_uses_inner_join():
    """When `?tag=foo` is supplied, the SQL must filter with an INNER
    JOIN against document_tags. Otherwise we'd over-fetch and discard
    in Python."""
    from app.api import documents as docs_mod
    import unittest.mock as _mock
    get_db, db = _make_db([
        [{"id": "doc-aaa", "title": "Alpha", "original_filename": "a.pdf",
          "file_type": "pdf", "created_at": "2026-06-01 00:00:00"}],
        [{"document_id": "doc-aaa", "tag": "research"}],
    ])
    with _mock.patch.object(docs_mod, "get_db", get_db), \
         _mock.patch.object(docs_mod, "get_current_user", _stub_current_user(1)):
        kind, _, body = asyncio.run(_call(docs_mod.list_documents(
            current_user={"id": 1, "username": "alice"}, tag="  #Research  ",
        )))
    check("list_documents ?tag=: ok", kind == "ok")
    check("list_documents ?tag=: returns 1 doc", len(body) == 1)
    sql, params = db.execute_calls[0]
    check("list_documents ?tag=: uses INNER JOIN", "INNER JOIN document_tags" in sql)
    check("list_documents ?tag=: passes normalised tag in params",
          "research" in params)


def test_list_documents_blank_tag_filter_treated_as_no_filter():
    """Sending ?tag= (empty after normalising) should fall back to the
    unfiltered query rather than 400 — so the UI's "clear filter"
    button can just call the same endpoint with an empty value."""
    from app.api import documents as docs_mod
    import unittest.mock as _mock
    get_db, db = _make_db([
        [{"id": "doc-aaa", "title": "Alpha", "original_filename": "a.pdf",
          "file_type": "pdf", "created_at": "2026-06-01 00:00:00"}],
        [],
    ])
    with _mock.patch.object(docs_mod, "get_db", get_db), \
         _mock.patch.object(docs_mod, "get_current_user", _stub_current_user(1)):
        kind, _, body = asyncio.run(_call(docs_mod.list_documents(
            current_user={"id": 1, "username": "alice"}, tag="   ",
        )))
    check("list_documents blank tag: ok", kind == "ok")
    sql = db.execute_calls[0][0]
    check("list_documents blank tag: no JOIN used",
          "INNER JOIN" not in sql)


# =========================================================================
# Pydantic models
# =========================================================================

def test_tag_create_validation():
    """TagCreate requires non-empty tag (Pydantic enforces 1..64)."""
    from pydantic import ValidationError
    try:
        TagCreate(tag="")
        check("TagCreate: empty tag rejected", False)
    except ValidationError:
        check("TagCreate: empty tag rejected", True)
    try:
        TagCreate(tag="x" * 65)
        check("TagCreate: 65-char tag rejected", False)
    except ValidationError:
        check("TagCreate: 65-char tag rejected", True)
    check("TagCreate: 'a' accepted", TagCreate(tag="a").tag == "a")


# =========================================================================
# Driver
# =========================================================================

ALL_TESTS = [
    test_normalize_tag_lowercases,
    test_normalize_tag_trims_whitespace,
    test_normalize_tag_strips_leading_hash,
    test_normalize_tag_blank_returns_none,
    test_normalize_tag_handles_unicode,
    test_list_tags_returns_sorted_list,
    test_list_tags_404_on_missing_doc,
    test_add_tag_normalises_and_inserts,
    test_add_tag_blank_returns_400,
    test_add_tag_404_on_missing_doc,
    test_remove_tag_normalises_path_param,
    test_remove_tag_blank_returns_400,
    test_remove_tag_is_idempotent_on_missing_tag,
    test_list_user_tags_groups_and_counts,
    test_list_user_tags_user_isolation,
    test_list_user_tags_with_q_substring_filter,
    test_list_documents_includes_tags_in_response,
    test_list_documents_with_no_tags_returns_empty_list,
    test_list_documents_tag_filter_uses_inner_join,
    test_list_documents_blank_tag_filter_treated_as_no_filter,
    test_tag_create_validation,
]


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks for #11 document tags...")
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
