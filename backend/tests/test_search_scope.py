"""Tests for retrieval-scope filtering (FEAT-026).

Layers:
  * chroma ``_where_for`` — the pure where-clause composer (None = no
    filter; a non-empty document list composes $and+$in; an EMPTY list
    must raise, because chroma's ``$in: []`` throws and because an empty
    scope must be short-circuited by callers, never widened to "no filter").
  * ``search_scope.resolve_document_ids_for_filter`` — tag → document_id
    resolution against document_tags, intersected with explicit ids.
  * Handlers — POST /api/search resolves the scope and threads it into
    retrieve(); a resolved-EMPTY scope short-circuits without calling
    retrieve at all (empty result, no billable pipeline).
"""
import asyncio

import pytest
from app.database import get_db, init_db


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    """Throwaway SQLite per test (suite-wide pattern)."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "search_scope_test.db"))
    from app.config import get_settings

    get_settings.cache_clear()
    asyncio.run(init_db())
    yield tmp_path
    get_settings.cache_clear()


# =========================================================================
# chroma where composer (pure)
# =========================================================================

def test_where_for_no_filter_is_user_only():
    from app.services.chroma_client import _where_for

    assert _where_for(1, None) == {"user_id": "1"}


def test_where_for_document_filter_composes_and_in():
    from app.services.chroma_client import _where_for

    where = _where_for(7, ["d2", "d1", "d2"])
    assert where == {
        "$and": [
            {"user_id": "7"},
            {"document_id": {"$in": ["d1", "d2"]}},
        ]
    }


def test_where_for_empty_filter_raises():
    """An empty scope means "nothing allowed". Callers must short-circuit;
    if one slips through, raising beats silently widening to no-filter."""
    from app.services.chroma_client import _where_for

    with pytest.raises(ValueError):
        _where_for(1, [])


# =========================================================================
# search_scope service
# =========================================================================

async def _bootstrap_tags():
    await init_db()
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'u1', 'x')"
        )
        for doc_id in ("doc-1", "doc-2", "doc-3"):
            await db.execute(
                "INSERT OR IGNORE INTO documents (id, user_id, title, original_filename, status) "
                f"VALUES ('{doc_id}', 1, 't', 'f.md', 'completed')"
            )
        rows = [
            ("doc-1", 1, "ai"),
            ("doc-2", 1, "ai"),
            ("doc-3", 1, "bio"),
        ]
        for doc_id, user_id, tag in rows:
            await db.execute(
                "INSERT OR IGNORE INTO document_tags (document_id, user_id, tag) VALUES (?, ?, ?)",
                (doc_id, user_id, tag),
            )
        # Another user's tag must never leak into user 1's scope.
        await db.execute(
            "INSERT OR IGNORE INTO documents (id, user_id, title, original_filename, status) "
            "VALUES ('doc-x', 2, 't', 'g.md', 'completed')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO document_tags (document_id, user_id, tag) VALUES ('doc-x', 2, 'ai')"
        )
        await db.commit()


def test_resolve_scope_no_filter_returns_none():
    from app.services.search_scope import resolve_document_ids_for_filter

    asyncio.run(_bootstrap_tags())
    assert asyncio.run(resolve_document_ids_for_filter(1)) is None


def test_resolve_scope_tag_resolves_normalized():
    from app.services.search_scope import resolve_document_ids_for_filter

    asyncio.run(_bootstrap_tags())
    got = asyncio.run(resolve_document_ids_for_filter(1, tag="#AI"))
    assert got == ["doc-1", "doc-2"]


def test_resolve_scope_document_ids_only():
    from app.services.search_scope import resolve_document_ids_for_filter

    asyncio.run(_bootstrap_tags())
    got = asyncio.run(resolve_document_ids_for_filter(1, document_ids=["doc-3", "doc-1", "doc-3"]))
    assert got == ["doc-1", "doc-3"]


def test_resolve_scope_tag_and_ids_intersect():
    from app.services.search_scope import resolve_document_ids_for_filter

    asyncio.run(_bootstrap_tags())
    got = asyncio.run(resolve_document_ids_for_filter(
        1, tag="ai", document_ids=["doc-1", "doc-3"],
    ))
    assert got == ["doc-1"]  # doc-3 is tagged bio, not ai


def test_resolve_scope_unknown_tag_is_empty_not_none():
    """A tag that matches nothing means "nothing allowed" — an empty list,
    NOT None (which would widen the query to the whole library)."""
    from app.services.search_scope import resolve_document_ids_for_filter

    asyncio.run(_bootstrap_tags())
    assert asyncio.run(resolve_document_ids_for_filter(1, tag="不存在")) == []


def test_resolve_scope_user_isolation():
    from app.services.search_scope import resolve_document_ids_for_filter

    asyncio.run(_bootstrap_tags())
    # doc-x is tagged ai by user 2 only — user 1 must not see it.
    assert asyncio.run(resolve_document_ids_for_filter(1, tag="ai")) == ["doc-1", "doc-2"]


# =========================================================================
# POST /api/search handler wiring
# =========================================================================

def test_search_handler_threads_document_ids_into_retrieve():
    from unittest import mock

    from app.api import search as search_mod
    from app.models.chat import SearchRequest

    seen = {}

    async def fake_retrieve(*args, **kwargs):
        seen.update(kwargs)
        return {"chunks": [], "entities": [], "relations": []}

    with mock.patch("app.services.retriever.retrieve", fake_retrieve):
        asyncio.run(search_mod.search(
            request=SearchRequest(query="q", top_k=5, tag="ai"),
            current_user={"id": 1},
        ))

    assert seen.get("document_ids") == ["doc-1", "doc-2"]


def test_search_handler_empty_scope_short_circuits():
    """An unknown tag resolves to an empty scope: the handler must return an
    empty result WITHOUT running the (billable) pipeline."""
    from unittest import mock

    from app.api import search as search_mod
    from app.models.chat import SearchRequest

    asyncio.run(_bootstrap_tags())

    def boom(*args, **kwargs):
        raise AssertionError("empty scope must not run the pipeline")

    with mock.patch("app.services.retriever.retrieve", boom):
        resp = asyncio.run(search_mod.search(
            request=SearchRequest(query="q", top_k=5, tag="不存在"),
            current_user={"id": 1},
        ))

    assert resp["chunks"] == []
    assert resp["entities"] == []
    assert resp["relations"] == []


def test_search_request_accepts_filter_fields():
    """Additive request fields: constructible, default None → old clients
    (which never send them) keep today's whole-library behaviour."""
    from app.models.chat import ChatRequest, SearchRequest

    req = SearchRequest(query="q", top_k=5, document_ids=["d1"], tag="x")
    assert req.document_ids == ["d1"]
    assert req.tag == "x"
    assert SearchRequest(query="q").document_ids is None
    assert SearchRequest(query="q").tag is None
    assert ChatRequest(message="m").document_ids is None
    assert ChatRequest(message="m").tag is None
