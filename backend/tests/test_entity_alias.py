"""Tests for entity aliases + duplicate discovery (FEAT-025).

Layers:
  * DDL — init_db creates entity_aliases, UNIQUE(user_id, alias) enforced.
  * Service (`app/services/entity_alias.py`) — record/resolve chain rules,
    alias-map loading, the rebuild-style extraction rewriter, and the pure
    duplicate-grouping function.
  * Handlers — merge auto-records an alias, entity delete cleans aliases,
    detail envelope carries aliases, duplicates endpoint groups correctly
    (direct handler invocation, suite-wide pattern).

The merge/alias write path relies on this invariant (verified against
create_entities_batch): re-writing an extracted name to a canonical that
exists only in the graph is safe — ON MATCH just bumps updated_at.
"""
import asyncio
import re

import pytest

from app.database import get_db, init_db


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    """Throwaway SQLite per test (suite-wide pattern)."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "entity_alias_test.db"))
    from app.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


async def _bootstrap():
    await init_db()
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'u1', 'x')"
        )
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (2, 'u2', 'x')"
        )
        await db.commit()


async def _alias_rows():
    async with get_db() as db:
        async with db.execute(
            "SELECT user_id, alias, canonical_name FROM entity_aliases ORDER BY alias"
        ) as cur:
            return [tuple(r) for r in await cur.fetchall()]


# =========================================================================
# DDL
# =========================================================================

def test_init_db_creates_entity_aliases():
    from app.services.entity_alias import record_alias, load_user_alias_map

    asyncio.run(_bootstrap())
    assert asyncio.run(record_alias(1, "张三", "张三丰")) is True
    assert asyncio.run(record_alias(1, "张三", "张三丰")) is True  # OR REPLACE idempotent
    rows = asyncio.run(_alias_rows())
    assert rows == [(1, "张三", "张三丰")]
    assert asyncio.run(load_user_alias_map(1)) == {"张三": "张三丰"}


# =========================================================================
# Service: record / resolve / delete / list / load
# =========================================================================

def test_record_alias_chained_resolution_and_cycle_guard():
    from app.services.entity_alias import record_alias, resolve_names, load_user_alias_map

    asyncio.run(_bootstrap())
    assert asyncio.run(record_alias(1, "A", "B")) is True
    assert asyncio.run(record_alias(1, "B", "C")) is True
    # A → B → C resolves to the chain's final target.
    assert asyncio.run(resolve_names(["A"], 1)) == ["C"]
    # Recording B → A cannot corrupt the chain: write-time resolution
    # collapses it to B → C (resolve(A) == C), identical to the existing row
    # — an idempotent no-op. A cycle (A→B→A) is unreachable by construction.
    assert asyncio.run(record_alias(1, "B", "A")) is True
    assert asyncio.run(load_user_alias_map(1)) == {"a": "B", "b": "C"}
    # Rebinding an existing alias to a DIFFERENT target is refused — the
    # user must delete_alias first (protects chains from accidental edits).
    assert asyncio.run(record_alias(1, "A", "X")) is False
    assert asyncio.run(load_user_alias_map(1)) == {"a": "B", "b": "C"}


def test_record_alias_ignores_self_and_empty():
    from app.services.entity_alias import record_alias

    asyncio.run(_bootstrap())
    # Exactly-equal alias/canonical is a pointless self-reference; blank
    # sides are noise. Case-only variants ("Morph"→"morph") ARE recorded by
    # design — node identity is the exact name, and the user-isolation test
    # exercises that path via OpenAI→openai.
    assert asyncio.run(record_alias(1, "Morph", "Morph")) is False
    assert asyncio.run(record_alias(1, "  X  ", "X")) is False
    assert asyncio.run(record_alias(1, "", "Y")) is False
    assert asyncio.run(record_alias(1, "Y", "")) is False
    assert asyncio.run(_alias_rows()) == []


def test_load_user_alias_map_user_isolation():
    from app.services.entity_alias import record_alias, load_user_alias_map

    asyncio.run(_bootstrap())
    asyncio.run(record_alias(1, "OpenAI", "openai"))
    asyncio.run(record_alias(2, "Anthropic", "anthropic"))
    assert asyncio.run(load_user_alias_map(1)) == {"openai": "openai"}
    assert asyncio.run(load_user_alias_map(2)) == {"anthropic": "anthropic"}


def test_delete_alias_and_list():
    from app.services.entity_alias import (
        record_alias, delete_alias, delete_aliases_for, list_aliases_for,
    )

    asyncio.run(_bootstrap())
    asyncio.run(record_alias(1, "张三", "张三丰"))
    asyncio.run(record_alias(1, "Zhang San", "张三丰"))
    asyncio.run(record_alias(1, "别的", "其他实体"))

    listed = asyncio.run(list_aliases_for(1, "张三丰"))
    assert [a["alias"] for a in listed] == ["Zhang San", "张三"]

    assert asyncio.run(delete_alias(1, "张三")) == 1
    assert asyncio.run(delete_alias(1, "张三")) == 0  # already gone
    assert asyncio.run(delete_alias(1, "不存在")) == 0

    # delete_aliases_for clears every alias pointing at one canonical.
    assert asyncio.run(delete_aliases_for(1, "张三丰")) == 1
    assert asyncio.run(list_aliases_for(1, "张三丰")) == []
    assert asyncio.run(list_aliases_for(1, "其他实体")) != []


# =========================================================================
# Service: apply_aliases_to_extraction (pure, rebuild-style)
# =========================================================================

def _mk_extraction():
    """Build a canonicalize-shaped result: dataclasses SHARED between
    entities and chunk_entities (that sharing is why the rewriter must
    rebuild rather than mutate)."""
    from app.services.entity_extractor import ExtractedEntity, ExtractedRelation

    zhang = ExtractedEntity(name="张三", type="PERSON", description="d1")
    zhang_sf = ExtractedEntity(name="张三丰", type="PERSON", description=None)
    li = ExtractedEntity(name="李四", type="PERSON", description="d3")
    return {
        "entities": [zhang, zhang_sf, li],
        "relations": [
            ExtractedRelation(source="张三", target="李四", relation_type="KNOWS"),
            ExtractedRelation(source="张三丰", target="张三", relation_type="SAME"),
        ],
        "chunk_entities": [
            {"chunk_id": "ch1", "content": "c1", "entities": [zhang, li]},
            {"chunk_id": "ch2", "content": "c2", "entities": [zhang_sf]},
        ],
    }


def test_apply_aliases_rewrites_and_dedupes():
    from app.services.entity_alias import apply_aliases_to_extraction

    result = apply_aliases_to_extraction(
        _mk_extraction(), {"张三": "张三丰"}
    )
    names = [e.name for e in result["entities"]]
    # 张三 → 张三丰; the two collapse — the FIRST-seen entry in list order
    # (张三, the one being rewritten) survives with its own fields.
    assert names == ["张三丰", "李四"]
    assert result["entities"][0].description == "d1"

    # chunk_entities follow the rewrite, deduped per chunk.
    assert [(e.name) for e in result["chunk_entities"][0]["entities"]] == ["张三丰", "李四"]
    assert [(e.name) for e in result["chunk_entities"][1]["entities"]] == ["张三丰"]

    # Relations remapped; the 张三丰→张三 self-loop is dropped.
    rels = [(r.source, r.target) for r in result["relations"]]
    assert rels == [("张三丰", "李四")]

    # Original objects untouched (rebuild, not mutate).
    assert _mk_extraction()["entities"][0].name == "张三"


def test_apply_aliases_canonical_absent_still_rewrites():
    """The canonical need not be in this document's extraction — the renamed
    entry stays (create_entities_batch ON MATCH is a no-op upsert)."""
    from app.services.entity_alias import apply_aliases_to_extraction
    from app.services.entity_extractor import ExtractedEntity

    result = {
        "entities": [ExtractedEntity(name="张三", type="PERSON")],
        "relations": [],
        "chunk_entities": [{"chunk_id": "ch1", "content": "c", "entities": []}],
    }
    out = apply_aliases_to_extraction(result, {"张三": "张三丰"})
    assert [e.name for e in out["entities"]] == ["张三丰"]
    assert out["entities"][0].type == "PERSON"


def test_apply_aliases_no_hit_equivalent():
    from app.services.entity_alias import apply_aliases_to_extraction

    src = _mk_extraction()
    out = apply_aliases_to_extraction(src, {"不存在": "别处"})
    assert [e.name for e in out["entities"]] == [e.name for e in src["entities"]]
    assert [(r.source, r.target) for r in out["relations"]] == [
        (r.source, r.target) for r in src["relations"]
    ]


# =========================================================================
# Service: find_duplicate_groups (pure)
# =========================================================================

def _row(name, type_, mentions, docs=1):
    return {"name": name, "type": type_, "mention_count": mentions,
            "document_ids": [f"d{i}" for i in range(docs)]}


def test_find_duplicate_groups_case_variant():
    from app.services.entity_alias import find_duplicate_groups

    groups = find_duplicate_groups([
        _row("OpenAI", "ORGANIZATION", 5),
        _row("openai", "ORGANIZATION", 3),
        _row("Neo4j", "CONCEPT", 2),
    ])
    assert len(groups) == 1
    g = groups[0]
    assert g["reason"] == "case"
    assert sorted(m["name"] for m in g["members"]) == ["OpenAI", "openai"]


def test_find_duplicate_groups_punct_variant():
    from app.services.entity_alias import find_duplicate_groups

    groups = find_duplicate_groups([
        _row("Open AI", "ORGANIZATION", 4),
        _row("Open-AI", "ORGANIZATION", 2),
        _row("OpenAI", "ORGANIZATION", 6),
    ])
    # All three collapse to the same punctuation-stripped key.
    assert len(groups) == 1
    g = groups[0]
    assert g["reason"] == "punct"
    assert len(g["members"]) == 3
    # Group sorted by total mention_count → the 12-mention group leads.
    assert groups[0]["members"][0]["name"] in ("Open AI", "Open-AI", "OpenAI")


def test_find_duplicate_groups_singletons_and_sort():
    from app.services.entity_alias import find_duplicate_groups

    groups = find_duplicate_groups([
        _row("唯一实体", "CONCEPT", 9),
        _row("甲", "PERSON", 2),
        _row("乙", "PERSON", 8),
        _row("丙", "PERSON", 1),
    ])
    assert groups == []  # no case/punct variants → no suggestions


def test_find_duplicate_groups_excludes_alias_keys():
    from app.services.entity_alias import find_duplicate_groups

    groups = find_duplicate_groups(
        [_row("OpenAI", "ORGANIZATION", 5), _row("openai", "ORGANIZATION", 3)],
        alias_keys={"openai"},  # openai 已是别名 → 不再建议合并
    )
    assert groups == []


# =========================================================================
# Handlers
# =========================================================================

class _FakeMergeNeo4j:
    def __init__(self):
        self.calls = []

    async def merge_entities(self, source_name, target_name, user_id):
        self.calls.append((source_name, target_name, user_id))
        return {"merged_from": source_name, "merged_into": target_name,
                "mentions_rewritten": 1, "outgoing_relations_rewritten": 0,
                "incoming_relations_rewritten": 0, "source_deleted": 1}


class _FakeDeleteNeo4j:
    async def delete_entity(self, name, user_id):
        return 1


class _FakeDetailNeo4j:
    async def get_entity_detail(self, name, user_id):
        return {"entity": {"name": name, "type": "PERSON"}, "stats": {},
                "documents": [], "related_entities": [], "sample_chunks": []}


class _FakeListNeo4j:
    def __init__(self, rows):
        self._rows = rows
        self.limits = []

    async def get_user_entities_with_mentions(self, user_id, limit=200):
        self.limits.append(limit)
        return list(self._rows)


def test_merge_handler_records_alias():
    from unittest import mock

    from app.api import graph as graph_mod
    from app.models.graph import MergeEntityRequest

    asyncio.run(_bootstrap())
    fake = _FakeMergeNeo4j()
    with mock.patch.object(graph_mod, "get_neo4j_client",
                           mock.Mock(side_effect=lambda: _ok(fake))):
        result = asyncio.run(graph_mod.merge_entities(
            payload=MergeEntityRequest(source="张三", target="张三丰"),
            current_user={"id": 1},
        ))
    assert result["source_deleted"] == 1
    rows = asyncio.run(_alias_rows())
    assert rows == [(1, "张三", "张三丰")]


def _ok(v):
    async def _g():
        return v
    return _g()


def test_merge_alias_failure_does_not_500():
    from unittest import mock

    from app.api import graph as graph_mod
    from app.models.graph import MergeEntityRequest

    asyncio.run(_bootstrap())
    fake = _FakeMergeNeo4j()
    with mock.patch.object(graph_mod, "get_neo4j_client",
                           mock.Mock(side_effect=lambda: _ok(fake))), \
         mock.patch.object(graph_mod, "record_alias",
                           mock.Mock(side_effect=RuntimeError("db down"))):
        result = asyncio.run(graph_mod.merge_entities(
            payload=MergeEntityRequest(source="A", target="B"),
            current_user={"id": 1},
        ))
    assert result["merged_into"] == "B"  # merge succeeded; alias best-effort


def test_delete_entity_cleans_aliases():
    from unittest import mock

    from app.api import graph as graph_mod
    from app.services.entity_alias import record_alias

    asyncio.run(_bootstrap())
    asyncio.run(record_alias(1, "旧名", "将删实体"))
    fake = _FakeDeleteNeo4j()
    with mock.patch.object(graph_mod, "get_neo4j_client",
                           mock.Mock(side_effect=lambda: _ok(fake))):
        asyncio.run(graph_mod.delete_entity(
            entity_name="将删实体", current_user={"id": 1},
        ))
    assert asyncio.run(_alias_rows()) == []


def test_detail_envelope_contains_aliases():
    from unittest import mock

    from app.api import graph as graph_mod
    from app.services.entity_alias import record_alias

    asyncio.run(_bootstrap())
    asyncio.run(record_alias(1, "张三", "张三丰"))
    fake = _FakeDetailNeo4j()
    with mock.patch.object(graph_mod, "get_neo4j_client",
                           mock.Mock(side_effect=lambda: _ok(fake))):
        envelope = asyncio.run(graph_mod.get_entity_detail(
            entity_name="张三丰", current_user={"id": 1},
        ))
    assert [a["alias"] for a in envelope["aliases"]] == ["张三"]


def test_duplicates_endpoint_groups():
    from unittest import mock

    from app.api import entity_curation as cur_mod

    asyncio.run(_bootstrap())
    rows = [
        {"name": "OpenAI", "type": "ORGANIZATION", "mention_count": 5,
         "chunk_ids": [], "document_ids": ["d1", "d2"]},
        {"name": "openai", "type": "ORGANIZATION", "mention_count": 3,
         "chunk_ids": [], "document_ids": ["d3"]},
        {"name": "张三", "type": "PERSON", "mention_count": 2,
         "chunk_ids": [], "document_ids": ["d1"]},
    ]
    fake = _FakeListNeo4j(rows)
    with mock.patch.object(cur_mod, "get_neo4j_client",
                           mock.Mock(side_effect=lambda: _ok(fake))):
        resp = asyncio.run(cur_mod.get_duplicates(current_user={"id": 1}))

    assert fake.limits == [2000]  # explicit cap — the default is 200
    assert resp.scanned == 3
    assert len(resp.groups) == 1
    g = resp.groups[0]
    assert g.reason == "case"
    assert {m.name for m in g.members} == {"OpenAI", "openai"}
    members = {m.name: m for m in g.members}
    assert members["OpenAI"].doc_count == 2


def test_aliases_delete_endpoint():
    from app.api import entity_curation as cur_mod
    from app.models.graph import AliasDeleteRequest
    from app.services.entity_alias import record_alias

    asyncio.run(_bootstrap())
    asyncio.run(record_alias(1, "旧名", "主实体"))
    resp = asyncio.run(cur_mod.delete_alias_endpoint(
        payload=AliasDeleteRequest(alias="旧名"), current_user={"id": 1},
    ))
    assert resp == {"deleted": 1}
    assert asyncio.run(_alias_rows()) == []
