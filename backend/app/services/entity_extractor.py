"""Entity and relation extraction service combining rules and LLM."""
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import jieba
import jieba.posseg as pseg

from app.config import get_settings
from app.services.llm import _normalize_entity_type, get_llm_service

logger = logging.getLogger(__name__)


def _dedupe_key(name: str) -> str:
    """Canonical identity key for an entity name.

    Neo4j Entity nodes are keyed by exact ``name`` + user_id, so case
    variants like "Python"/"python" would become separate nodes. Collapsing
    on the lowercased name keeps the Python-side de-duplication and reference
    remapping consistent with that single-node-per-name identity.
    """
    return name.strip().lower()


def canonicalize_extraction_results(
    entities: List["ExtractedEntity"],
    chunk_entities: List[Dict[str, Any]],
    relations: List["ExtractedRelation"],
) -> Dict[str, Any]:
    """Collapse case-variant entity names onto one canonical node per name.

    Extraction is per-chunk, so the same entity can appear in several chunks
    under slightly different spellings ("Python" vs "python"). The graph
    stores ONE node per name, so everything downstream (the entity payload,
    the per-chunk MENTIONS links, and the RELATES_TO endpoints) must reference
    the same canonical spelling — otherwise MENTIONS' MERGE would auto-create
    phantom nodes for variants the dedupe discarded, and relation edges would
    silently miss on the exact-name MATCH.

    First-seen (document chunk order) spelling wins as the canonical name;
    the type/description of that first-seen entry are canonical too.

    Returns {"entities", "relations", "chunk_entities"} with every name
    remapped, matching the shape ``process_chunks`` returns.
    """
    canon: Dict[str, ExtractedEntity] = {}
    for entity in entities:
        key = _dedupe_key(entity.name)
        if key not in canon:
            canon[key] = entity

    unique_entities = list(canon.values())

    canonical_chunks = []
    for cd in chunk_entities:
        seen: set = set()
        canonical_ents: List[ExtractedEntity] = []
        for entity in cd.get("entities", []):
            canonical = canon.get(_dedupe_key(entity.name))
            if canonical is None or canonical.name in seen:
                continue
            seen.add(canonical.name)
            canonical_ents.append(canonical)
        canonical_chunks.append({**cd, "entities": canonical_ents})

    canonical_relations: List[ExtractedRelation] = []
    for relation in relations:
        src = canon.get(_dedupe_key(relation.source))
        tgt = canon.get(_dedupe_key(relation.target))
        if src is None or tgt is None or src.name == tgt.name:
            continue
        canonical_relations.append(ExtractedRelation(
            source=src.name,
            target=tgt.name,
            relation_type=relation.relation_type,
            relation_source=relation.relation_source,
        ))

    return {
        "entities": unique_entities,
        "relations": canonical_relations,
        "chunk_entities": canonical_chunks,
    }


@dataclass
class ExtractedEntity:
    """Extracted entity data."""
    name: str
    type: str
    description: Optional[str] = None
    source: str = "rule"  # "rule" or "llm"


@dataclass
class ExtractedRelation:
    """Extracted relation data."""
    source: str
    target: str
    relation_type: str
    relation_source: str = "rule"


class RuleBasedExtractor:
    """Rule-based entity extraction using regex and jieba."""

    # Entity type patterns
    PATTERNS = {
        "PERSON": [
            r'[\u4e00-\u9fa5]{2,4}(?:先生|女士|教授|博士|医生|老师)',
            r'[A-Z][a-z]+\s+[A-Z][a-z]+',
        ],
        "ORGANIZATION": [
            r'[\u4e00-\u9fa5]{2,8}(?:公司|集团|银行|学校|大学|医院|研究所|中心)',
            r'(?:Google|Apple|Microsoft|Amazon|Facebook|Meta|Tencent|Alibaba|ByteDance)[\w\s]*',
        ],
        "LOCATION": [
            r'[\u4e00-\u9fa5]{2,6}(?:省|市|县|区|镇|村)',
            r'(?:北京|上海|广州|深圳|杭州|南京|成都|武汉|西安|重庆)',
            r'(?:China|USA|UK|Japan|Germany|France|Canada|Australia)',
        ],
        "TIME": [
            r'\d{4}年(?:\d{1,2}月)?(?:\d{1,2}[日号])?',
            r'(?:19|20)\d{2}(?:-\d{2})?',
            r'(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s*\d{4}',
        ],
    }

    # Generic words the LLM prompt tells the model to skip. The rule layer
    # has no notion of "core subject", so it mirrors the same list to keep
    # rule-based CONCEPT noise aligned with LLM behaviour.
    SKIP_WORDS = frozenset({
        "系统", "用户", "信息", "方法",
        "system", "user", "data", "method",
    })

    def extract(self, text: str) -> List[ExtractedEntity]:
        """Extract entities using rules."""
        entities = []
        extracted_names = set()

        # Regex-based extraction
        for entity_type, patterns in self.PATTERNS.items():
            for pattern in patterns:
                for match in re.finditer(pattern, text):
                    name = match.group(0)
                    if name not in extracted_names:
                        entities.append(ExtractedEntity(
                            name=name,
                            type=entity_type,
                            description=None,
                            source="rule"
                        ))
                        extracted_names.add(name)

        # Jieba-based extraction
        words = pseg.cut(text)
        for word, flag in words:
            if len(word) >= 2 and word not in extracted_names:
                # Map jieba flags to entity types
                if flag.startswith('nr'):  # Person name
                    entities.append(ExtractedEntity(
                        name=word,
                        type="PERSON",
                        source="rule"
                    ))
                    extracted_names.add(word)
                elif flag.startswith('ns'):  # Location
                    entities.append(ExtractedEntity(
                        name=word,
                        type="LOCATION",
                        source="rule"
                    ))
                    extracted_names.add(word)
                elif flag.startswith('nt'):  # Organization
                    entities.append(ExtractedEntity(
                        name=word,
                        type="ORGANIZATION",
                        source="rule"
                    ))
                    extracted_names.add(word)
                elif flag.startswith('n') and len(word) >= 3 and word.lower() not in self.SKIP_WORDS:  # General noun
                    entities.append(ExtractedEntity(
                        name=word,
                        type="CONCEPT",
                        source="rule"
                    ))
                    extracted_names.add(word)

        return entities


class EntityExtractor:
    """Combined entity and relation extraction service."""

    def __init__(self):
        self.rule_extractor = RuleBasedExtractor()
        self.settings = get_settings()

    async def _extract_entities_and_relations_llm(
        self, chunks: List[Any]
    ) -> Dict[str, Dict[str, List]]:
        """Extract entities AND relations for every chunk via ONE LLM call
        per chunk — see LLMService.extract_entities_and_relations_batch for
        why this replaces the old two-stage design (2N calls + a barrier
        where no relation call could start until every chunk's entities
        were back).

        Returns {chunk_id: {"entities": [ExtractedEntity],
                            "relations": [ExtractedRelation]}}.

        On total failure every chunk maps to empty lists, so the pipeline
        degrades to "document indexed without a graph" instead of failing
        the upload.
        """
        llm_service = await get_llm_service()
        texts = [c.content for c in chunks]
        try:
            raw_results = await llm_service.extract_entities_and_relations_batch(texts)
        except Exception as e:
            logger.warning(
                "LLM combined extraction failed; returning empty results: %s",
                e, exc_info=True,
            )
            return {c.chunk_id: {"entities": [], "relations": []} for c in chunks}

        results: Dict[str, Dict[str, List]] = {}
        raw_relation_total = 0
        kept_relation_total = 0
        for chunk, raw in zip(chunks, raw_results):
            entity_dicts = raw.get("entities", []) if isinstance(raw, dict) else []
            relation_dicts = raw.get("relations", []) if isinstance(raw, dict) else []
            raw_relation_total += len(relation_dicts)

            entities = [
                ExtractedEntity(
                    name=str(e.get("name", "")).strip(),
                    type=_normalize_entity_type(e.get("type")),
                    description=e.get("description"),
                    source="llm",
                )
                for e in entity_dicts
                if str(e.get("name") or "").strip()
            ]

            # A relation is only kept when both endpoints name an entity
            # extracted from THIS chunk. The LLM is told to constrain edges to
            # its own entity array, so a cross-chunk relation can never be
            # produced here — this filter just enforces the contract. Matching
            # is case-insensitive because spelling may vary; the raw names ride
            # along and get remapped to the canonical spelling later, so no
            # edge is lost to case drift. Anything dropped is counted so the
            # graph-sparsity trade-off stays observable.
            known_names = {_dedupe_key(e.name) for e in entities}
            relations: List[ExtractedRelation] = []
            for r in relation_dicts:
                source = str(r.get("source") or "").strip()
                target = str(r.get("target") or "").strip()
                if not source or not target or source == target:
                    continue
                if _dedupe_key(source) not in known_names or _dedupe_key(target) not in known_names:
                    continue
                relations.append(ExtractedRelation(
                    source=source,
                    target=target,
                    relation_type=r.get("relation_type") or "MENTIONS",
                    relation_source="llm",
                ))
            kept_relation_total += len(relations)

            results[chunk.chunk_id] = {"entities": entities, "relations": relations}

        if raw_relation_total > kept_relation_total:
            logger.info(
                "Dropped %d/%d candidate relations not local to a single chunk "
                "(self-loops or non-chunk endpoints)",
                raw_relation_total - kept_relation_total, raw_relation_total,
            )
        return results

    def _merge_entity_results(
        self, rule_entities: List[ExtractedEntity], llm_entities: List[ExtractedEntity]
    ) -> List[ExtractedEntity]:
        """Merge rule and LLM entities, preferring LLM results.

        Keyed by the canonical name only (no type): the graph stores one node
        per name, so two extractions of the same name under different types
        are the same entity, and the LLM entry is authoritative.
        """
        entity_dict = {}

        for entity in rule_entities:
            entity_dict[_dedupe_key(entity.name)] = entity

        for entity in llm_entities:
            entity_dict[_dedupe_key(entity.name)] = entity

        return list(entity_dict.values())

    async def process_chunks(self, chunks: List[Any], use_rule_extraction: bool = False) -> Dict[str, Any]:
        """Process multiple chunks to extract entities and relations.

        Args:
            chunks: List of chunks to process
            use_rule_extraction: If True, merge rule-based entities in first.
                                 If False, use LLM only (faster, recommended).

        流程：
        1. 规则提取（可选）- 快速获得基础实体
        2. 每 chunk 一次合并 LLM 调用，同时返回实体和关系
           （旧设计是「N 次实体调用 → stage barrier → N 次关系调用」，
           调用数翻倍且关系抽取必须等全部实体返回；合并后 LLM 往返
           减半、全程并发重叠）
        3. 合并规则 + LLM 实体并去重
        """
        logger.info("Processing %d chunks for entity extraction...", len(chunks))

        # Stage 1: Rule-based extraction (only if enabled)
        rule_results = {}
        if use_rule_extraction:
            for chunk in chunks:
                rule_results[chunk.chunk_id] = self.rule_extractor.extract(chunk.content)

        # Stage 2: ONE combined LLM call per chunk (entities + relations)
        llm_results: Dict[str, Dict[str, List]] = {}
        if self.settings.ENABLE_LLM_EXTRACTION:
            try:
                llm_results = await self._extract_entities_and_relations_llm(chunks)
                logger.info("LLM combined extraction completed: %d chunks", len(llm_results))
            except Exception as e:
                logger.warning("LLM combined extraction failed: %s", e)

        # Stage 3: 合并规则 + LLM 实体，收集关系
        all_entities = []
        all_relations = []
        chunk_entities = []
        for chunk in chunks:
            chunk_result = llm_results.get(chunk.chunk_id, {})
            rule_ents = rule_results.get(chunk.chunk_id, [])
            llm_ents = chunk_result.get("entities", [])
            merged = self._merge_entity_results(rule_ents, llm_ents)

            chunk_entities.append({
                "chunk_id": chunk.chunk_id,
                "content": chunk.content,
                "entities": merged
            })
            all_entities.extend(merged)
            all_relations.extend(chunk_result.get("relations", []))

        # Stage 4: 实体去重（规范化名唯一，与图内 name 身份一致）
        entity_dict = {}
        for entity in all_entities:
            key = _dedupe_key(entity.name)
            if key not in entity_dict:
                entity_dict[key] = entity

        unique_entities = list(entity_dict.values())
        logger.info(
            "Extraction totals: %d unique entities, %d relations",
            len(unique_entities), len(all_relations),
        )

        return {
            "entities": unique_entities,
            "relations": all_relations,
            "chunk_entities": chunk_entities
        }


# Singleton instance
_extractor: Optional[EntityExtractor] = None


async def get_entity_extractor() -> EntityExtractor:
    """Get singleton entity extractor instance."""
    global _extractor
    if _extractor is None:
        _extractor = EntityExtractor()
    return _extractor
