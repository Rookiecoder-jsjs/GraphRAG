"""Unit tests for the entity extraction pipeline (offline, no DB/LLM).

Covers the canonical-name identity rule: case-variant spellings collapse to
one node per name, every reference (chunk MENTIONS, RELATES_TO endpoints) is
remapped to the canonical spelling, entity types fold onto a controlled
vocabulary, and cross-chunk / self-loop relations are dropped with a count
(logged, not silent).
"""
import asyncio
import logging

import pytest

from app.services import entity_extractor as ee
from app.services.entity_extractor import (
    EntityExtractor,
    ExtractedEntity,
    ExtractedRelation,
    RuleBasedExtractor,
    _dedupe_key,
    canonicalize_extraction_results,
)
from app.services.llm import _bounded_extraction_input, _normalize_entity_type


class _Chunk:
    def __init__(self, chunk_id, content):
        self.chunk_id = chunk_id
        self.content = content


class TestNormalizeEntityType:
    def test_canonical_english_passthrough(self):
        assert _normalize_entity_type("PERSON") == "PERSON"
        assert _normalize_entity_type("ORGANIZATION") == "ORGANIZATION"
        assert _normalize_entity_type("LOCATION") == "LOCATION"
        assert _normalize_entity_type("CONCEPT") == "CONCEPT"
        assert _normalize_entity_type("EVENT") == "EVENT"
        assert _normalize_entity_type("TIME") == "TIME"

    def test_synonyms_folded(self):
        assert _normalize_entity_type("机构") == "ORGANIZATION"
        assert _normalize_entity_type("公司") == "ORGANIZATION"
        assert _normalize_entity_type("地点") == "LOCATION"
        assert _normalize_entity_type("人名") == "PERSON"
        assert _normalize_entity_type(" 机构 ") == "ORGANIZATION"

    def test_unknown_and_missing_default_to_other(self):
        assert _normalize_entity_type("随便写的类型") == "OTHER"
        assert _normalize_entity_type(None) == "OTHER"
        assert _normalize_entity_type("") == "OTHER"


class TestBoundedInput:
    def test_short_text_untouched(self):
        text = "a" * 100
        assert _bounded_extraction_input(text) == text

    def test_oversized_text_gets_truncation_marker(self, caplog):
        text = "a" * 3000
        with caplog.at_level(logging.WARNING):
            out = _bounded_extraction_input(text)
        assert len(out) < 2100
        assert "truncated" in out


class TestRuleExtractorSkipsGenericWords:
    def test_no_skip_word_surfaces_as_concept(self):
        text = "系统 user 信息 方法 数据管理 算法设计 机器学习"
        entities = RuleBasedExtractor().extract(text)
        concepts = {e.name.lower() for e in entities if e.type == "CONCEPT"}
        assert concepts.isdisjoint(RuleBasedExtractor.SKIP_WORDS)


def _entity(name, type_="CONCEPT", source="llm"):
    return ExtractedEntity(name=name, type=type_, description=None, source=source)


class TestCanonicalizeExtractionResults:
    def test_case_variants_collapse_to_first_seen(self):
        entities = [_entity("Python"), _entity("python")]
        out = canonicalize_extraction_results(entities, [], [])
        assert len(out["entities"]) == 1
        assert out["entities"][0].name == "Python"

    def test_same_name_different_type_is_one_entity(self):
        entities = [_entity("苹果", type_="ORGANIZATION"), _entity("苹果", type_="CONCEPT")]
        out = canonicalize_extraction_results(entities, [], [])
        assert len(out["entities"]) == 1
        assert out["entities"][0].type == "ORGANIZATION"

    def test_chunk_entities_remapped_to_canonical_name(self):
        entities = [_entity("Python")]
        chunk_entities = [
            {"chunk_id": "c1", "content": "x", "entities": [_entity("python")]}
        ]
        out = canonicalize_extraction_results(entities, chunk_entities, [])
        chunk = out["chunk_entities"][0]
        assert [e.name for e in chunk["entities"]] == ["Python"]

    def test_relation_endpoints_remapped_and_self_loop_dropped(self):
        entities = [_entity("Python"), _entity("Java")]
        relations = [
            ExtractedRelation(source="python", target="JAVA", relation_type="USES"),
            ExtractedRelation(source="Python", target="python", relation_type="RELATED_TO"),
        ]
        out = canonicalize_extraction_results(entities, [], relations)
        rels = out["relations"]
        assert len(rels) == 1
        assert (rels[0].source, rels[0].target) == ("Python", "Java")

    def test_relation_with_unknown_endpoint_dropped(self):
        entities = [_entity("Python")]
        relations = [
            ExtractedRelation(source="Python", target="Ghost", relation_type="USES"),
        ]
        out = canonicalize_extraction_results(entities, [], relations)
        assert out["relations"] == []


class TestExtractEntitiesAndRelationsLlm:
    def _chunks(self):
        return [_Chunk("c1", "chunk one"), _Chunk("c2", "chunk two")]

    def _stub_llm(self, monkeypatch, results):
        class _Stub:
            async def extract_entities_and_relations_batch(self, texts):
                return results

        async def fake_get_llm_service():
            return _Stub()

        monkeypatch.setattr(ee, "get_llm_service", fake_get_llm_service)

    def test_types_normalized_and_case_insensitive_relations_kept(self, monkeypatch):
        self._stub_llm(monkeypatch, [
            {
                "entities": [
                    {"name": "Python", "type": "编程语言", "description": None},
                    {"name": "Java", "type": None, "description": None},
                ],
                "relations": [
                    {"source": "python", "target": "Java", "relation_type": "uses"},
                ],
            },
            {"entities": [], "relations": []},
        ])
        extractor = EntityExtractor()
        out = asyncio.run(extractor._extract_entities_and_relations_llm(self._chunks()))

        ents = out["c1"]["entities"]
        types = {e.name: e.type for e in ents}
        assert types["Python"] == "OTHER"
        assert types["Java"] == "OTHER"
        rels = out["c1"]["relations"]
        # Kept despite the case-variant "python" endpoint; raw spelling rides
        # through and canonicalize_extraction_results remaps it later.
        assert len(rels) == 1 and rels[0].source == "python"

    def test_cross_chunk_and_self_relations_dropped_and_logged(self, monkeypatch, caplog):
        self._stub_llm(monkeypatch, [
            {
                "entities": [
                    {"name": "A", "type": "CONCEPT"},
                    {"name": "B", "type": "CONCEPT"},
                ],
                "relations": [
                    {"source": "A", "target": "B", "relation_type": "USES"},   # kept
                    {"source": "A", "target": "A", "relation_type": "USES"},   # self
                    {"source": "A", "target": "Ghost", "relation_type": "USES"},  # not local
                ],
            },
            {"entities": [], "relations": []},
        ])
        extractor = EntityExtractor()
        with caplog.at_level(logging.INFO):
            out = asyncio.run(extractor._extract_entities_and_relations_llm(self._chunks()))

        assert len(out["c1"]["relations"]) == 1
        assert "Dropped 2/3 candidate relations" in caplog.text


class TestProcessChunks:
    async def _fake_combined(self, chunks):
        return {
            c.chunk_id: {
                "entities": [
                    ExtractedEntity(name=n, type=t, source="llm")
                    for n, t in [("Python", "CONCEPT"), ("java", "CONCEPT")]
                ] if c.chunk_id == "c1" else [],
                "relations": [
                    ExtractedRelation(source="python", target="java",
                                      relation_type="USES", relation_source="llm"),
                ] if c.chunk_id == "c1" else [],
            }
            for c in chunks
        }

    def test_process_chunks_returns_full_result_shape(self):
        extractor = EntityExtractor()
        extractor._extract_entities_and_relations_llm = self._fake_combined
        chunks = [_Chunk("c1", "py chunk"), _Chunk("c2", "java chunk")]
        result = asyncio.run(extractor.process_chunks(chunks, use_rule_extraction=False))

        assert set(result) == {"entities", "relations", "chunk_entities"}
        assert len(result["chunk_entities"]) == 2
        # Batch-level dedupe collapses the case variants to the first-seen name.
        names = {e.name for e in result["entities"]}
        assert names == {"Python", "java"}


class TestDedupeKey:
    def test_strips_and_lowercases(self):
        assert _dedupe_key("  Python  ") == "python"
        assert _dedupe_key("Python") == _dedupe_key("python")
