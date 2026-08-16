"""Entity and relation extraction service combining rules and LLM."""
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import jieba
import jieba.posseg as pseg

from app.config import get_settings
from app.services.llm import get_llm_service
from app.services.progress_tracker import get_progress_emitter

logger = logging.getLogger(__name__)


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
                elif flag.startswith('n') and len(word) >= 3:  # General noun
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

    async def extract_entities(self, text: str, use_llm: bool = True) -> List[ExtractedEntity]:
        """Extract entities from text."""
        # Stage 1: Rule-based extraction
        rule_entities = self.rule_extractor.extract(text)

        if not use_llm:
            return rule_entities

        # Stage 2: LLM refinement
        try:
            llm_service = await get_llm_service()
            llm_results = await llm_service.extract_entities_batch([text])

            # Merge results
            rule_names = {e.name.lower() for e in rule_entities}
            for entity_data in llm_results[0] if llm_results else []:
                name = entity_data.get("name", "").strip()
                if name and name.lower() not in rule_names:
                    rule_entities.append(ExtractedEntity(
                        name=name,
                        type=entity_data.get("type", "OTHER"),
                        description=entity_data.get("description"),
                        source="llm"
                    ))

        except Exception as e:
            # Fall back to rule-based only, but log the failure for visibility.
            logger.warning(
                "LLM entity extraction failed; falling back to rule-based results: %s",
                e,
                exc_info=True,
            )

        return rule_entities

    async def extract_relations(self, text: str, entities: List[ExtractedEntity]) -> List[ExtractedRelation]:
        """Extract relations between entities (co-occurrence heuristic).

        Not used by the main ingestion pipeline - ``process_chunks`` runs the
        combined LLM path (``_extract_entities_and_relations_llm``) instead.
        Kept for callers that want a cheap non-LLM relation pass.
        """
        relations = []

        if len(entities) < 2:
            logger.debug("Not enough entities for relations: %d", len(entities))
            return relations

        logger.debug(
            "Extracting relations from text (%d chars) for %d entities",
            len(text), len(entities),
        )

        # Simple co-occurrence based relations
        entity_names = [e.name for e in entities]
        entity_positions = {name: [] for name in entity_names}

        for name in entity_names:
            try:
                for match in re.finditer(re.escape(name), text):
                    entity_positions[name].append(match.start())
            except re.error:
                continue

        # Debug: log positions found
        for name, positions in entity_positions.items():
            if positions:
                logger.debug("Found '%s' at positions: %s", name, positions[:3])

        # Create relations for entities that appear close together
        created_pairs = set()
        for name1 in entity_names:
            for name2 in entity_names:
                if name1 >= name2:  # Skip self and duplicates
                    continue

                pair_key = tuple(sorted([name1, name2]))
                if pair_key in created_pairs:
                    continue

                positions1 = entity_positions.get(name1, [])
                positions2 = entity_positions.get(name2, [])

                if not positions1 or not positions2:
                    continue

                # Check if any positions are close
                for pos1 in positions1:
                    for pos2 in positions2:
                        if abs(pos1 - pos2) < 300:  # Within 300 characters
                            relations.append(ExtractedRelation(
                                source=name1,
                                target=name2,
                                relation_type="MENTIONS"
                            ))
                            created_pairs.add(pair_key)
                            logger.debug("Created relation: %s -> %s", name1, name2)
                            break
                    else:
                        continue
                    break

        logger.debug("Total relations extracted: %d", len(relations))
        return relations

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
        for chunk, raw in zip(chunks, raw_results):
            entity_dicts = raw.get("entities", []) if isinstance(raw, dict) else []
            relation_dicts = raw.get("relations", []) if isinstance(raw, dict) else []

            entities = [
                ExtractedEntity(
                    name=str(e.get("name", "")).strip(),
                    type=e.get("type") or "OTHER",
                    description=e.get("description"),
                    source="llm",
                )
                for e in entity_dicts
                if str(e.get("name") or "").strip()
            ]

            # Relations may only reference entities extracted from THIS
            # chunk. Anything else would be silently dropped later by
            # create_relations_batch's MATCH-by-name — filtering here keeps
            # the logged counts honest.
            known_names = {e.name for e in entities}
            relations: List[ExtractedRelation] = []
            for r in relation_dicts:
                source = str(r.get("source") or "").strip()
                target = str(r.get("target") or "").strip()
                if not source or not target or source == target:
                    continue
                if source not in known_names or target not in known_names:
                    continue
                relations.append(ExtractedRelation(
                    source=source,
                    target=target,
                    relation_type=r.get("relation_type") or "MENTIONS",
                    relation_source="llm",
                ))

            results[chunk.chunk_id] = {"entities": entities, "relations": relations}
        return results

    def _merge_entity_results(
        self, rule_entities: List[ExtractedEntity], llm_entities: List[ExtractedEntity]
    ) -> List[ExtractedEntity]:
        """Merge rule and LLM entities, preferring LLM results."""
        entity_dict = {}

        for entity in rule_entities:
            key = (entity.name.lower(), entity.type)
            entity_dict[key] = entity

        for entity in llm_entities:
            key = (entity.name.lower(), entity.type)
            entity_dict[key] = entity

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

        # Stage 4: 实体去重
        entity_dict = {}
        for entity in all_entities:
            key = (entity.name.lower(), entity.type)
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
