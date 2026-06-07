"""Entity and relation extraction service combining rules and LLM."""
import asyncio
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
        """Extract relations between entities."""
        relations = []

        if len(entities) < 2:
            print(f"   Not enough entities for relations: {len(entities)}")
            return relations

        print(f"   Extracting relations from text ({len(text)} chars) for {len(entities)} entities")

        # Simple co-occurrence based relations
        entity_names = [e.name for e in entities]
        entity_positions = {name: [] for name in entity_names}

        for name in entity_names:
            try:
                for match in re.finditer(re.escape(name), text):
                    entity_positions[name].append(match.start())
            except re.error:
                continue

        # Debug: print positions found
        for name, positions in entity_positions.items():
            if positions:
                print(f"   Found '{name}' at positions: {positions[:3]}")  # First 3 positions

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
                            print(f"   Created relation: {name1} -> {name2}")
                            break
                    else:
                        continue
                    break

        print(f"   Total relations extracted: {len(relations)}")
        return relations

    async def _extract_entities_batch_optimized(
        self, chunks: List[Any]
    ) -> Dict[str, List[ExtractedEntity]]:
        """Extract entities from chunks using LLM with batch processing."""
        chunk_entities = {}
        batch_size = self.settings.ENTITY_BATCH_SIZE  # 使用配置的批次大小
        llm_service = await get_llm_service()

        # Split into batches
        batches = []
        for i in range(0, len(chunks), batch_size):
            batches.append(chunks[i:i + batch_size])

        print(f"   Processing {len(chunks)} chunks in {len(batches)} batches (batch_size={batch_size})")

        async def _process_batch(batch: List[Any]) -> Dict[str, List[ExtractedEntity]]:
            result = {}
            texts = [c.content for c in batch]
            try:
                # 批量调用 LLM，一次处理多文本
                llm_results = await llm_service.extract_entities_batch(texts)
                for chunk, entities_data in zip(batch, llm_results):
                    result[chunk.chunk_id] = [
                        ExtractedEntity(
                            name=e.get("name", ""),
                            type=e.get("type", "OTHER"),
                            description=e.get("description"),
                            source="llm"
                        )
                        for e in entities_data if e.get("name")
                    ]
            except Exception as e:
                logger.warning("LLM entity batch failed; returning empty results: %s", e, exc_info=True)
                for chunk in batch:
                    result[chunk.chunk_id] = []
            return result

        # Run all batches concurrently
        tasks = [_process_batch(batch) for batch in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, dict):
                chunk_entities.update(result)

        return chunk_entities

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

    async def _extract_cooccurrence_relations(
        self, text: str, entities: List[ExtractedEntity]
    ) -> List[ExtractedRelation]:
        """Extract relations based on co-occurrence distance."""
        relations = []
        if len(entities) < 2:
            return relations

        entity_names = [e.name for e in entities]
        entity_positions = {name: [] for name in entity_names}

        for name in entity_names:
            try:
                for match in re.finditer(re.escape(name), text):
                    entity_positions[name].append(match.start())
            except re.error:
                continue

        created_pairs = set()
        for name1 in entity_names:
            for name2 in entity_names:
                if name1 >= name2:
                    continue

                pair_key = tuple(sorted([name1, name2]))
                if pair_key in created_pairs:
                    continue

                positions1 = entity_positions.get(name1, [])
                positions2 = entity_positions.get(name2, [])

                if not positions1 or not positions2:
                    continue

                for pos1 in positions1:
                    for pos2 in positions2:
                        if abs(pos1 - pos2) < 300:
                            relations.append(ExtractedRelation(
                                source=name1,
                                target=name2,
                                relation_type="MENTIONS"
                            ))
                            created_pairs.add(pair_key)
                            break
                    else:
                        continue
                    break

        return relations

    async def _extract_relations_llm_parallel(
        self,
        chunks: List[Any],
        chunk_entities: List[Dict],
        llm_service
    ) -> List[ExtractedRelation]:
        """Extract relations using LLM with batch processing."""
        print(f"   Extracting relations with LLM (batch mode)...")

        all_relations = []
        batch_size = self.settings.ENTITY_BATCH_SIZE

        # Split into batches
        rel_batches = []
        for i in range(0, len(chunks), batch_size):
            batch_chunks = chunks[i:i + batch_size]
            batch_chunk_entities = chunk_entities[i:i + batch_size]
            rel_batches.append((batch_chunks, batch_chunk_entities))

        print(f"   Processing {len(chunks)} chunks in {len(rel_batches)} relation batches")

        async def _extract_relations_batch(batch_chunks, batch_ce):
            texts = [c.content for c in batch_chunks]
            ents_list = [[{"name": e.name, "type": e.type} for e in cd["entities"]]
                       for cd in batch_ce]
            return await llm_service.extract_relations_batch(texts, ents_list)

        # Run all batches concurrently
        tasks = [_extract_relations_batch(bc, bce) for bc, bce in rel_batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                print(f"   Relation batch error: {result}")
                continue
            for relations_data in result:
                for rel in relations_data:
                    all_relations.append(ExtractedRelation(
                        source=rel.get("source", ""),
                        target=rel.get("target", ""),
                        relation_type=rel.get("relation_type", "MENTIONS"),
                        relation_source="llm"
                    ))

        print(f"   Total relations extracted: {len(all_relations)}")
        return all_relations

    async def _extract_relations_cooccurrence_parallel(
        self,
        chunks: List[Any],
        chunk_entities: List[Dict]
    ) -> List[ExtractedRelation]:
        """并行提取关系 - 共现模式"""
        print(f"   Extracting relations with co-occurrence (parallel)...")

        all_relations = []

        # 并行处理所有 chunk
        tasks = [
            self._extract_cooccurrence_relations(chunk.content, chunk_data["entities"])
            for chunk, chunk_data in zip(chunks, chunk_entities)
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                continue
            all_relations.extend(result)

        print(f"   Total relations extracted: {len(all_relations)}")
        return all_relations

    async def process_chunks(self, chunks: List[Any], use_rule_extraction: bool = False) -> Dict[str, Any]:
        """Process multiple chunks to extract entities and relations.

        Args:
            chunks: List of chunks to process
            use_rule_extraction: If True, use rule-based extraction first then LLM.
                                 If False, use LLM only (faster, recommended).

        流程：
        1. 规则提取（可选）- 快速获得基础实体
        2. LLM 实体提取 - 并发处理所有 chunks
        3. LLM 关系提取 - 基于提取的实体
        """
        print(f"   Processing {len(chunks)} chunks for entity extraction...")

        # Stage 1: Rule-based extraction (only if enabled)
        rule_results = {}
        if use_rule_extraction:
            print(f"   Stage 1: Rule-based entity extraction...")
            for chunk in chunks:
                rule_results[chunk.chunk_id] = self.rule_extractor.extract(chunk.content)

        # Stage 2: LLM 实体提取 (所有 chunks 并发)
        print(f"   Stage 2: LLM entity extraction (parallel)...")
        llm_results = {}
        llm_service = None

        if self.settings.ENABLE_LLM_EXTRACTION:
            try:
                llm_service = await get_llm_service()
                llm_results = await self._extract_entities_batch_optimized(chunks)
                print(f"   LLM entity extraction completed: {len(llm_results)} chunks")
            except Exception as e:
                print(f"   LLM entity extraction failed: {e}")

        # Stage 3: 合并规则和 LLM 实体
        all_entities = []
        chunk_entities = []
        for chunk in chunks:
            rule_ents = rule_results.get(chunk.chunk_id, [])
            llm_ents = llm_results.get(chunk.chunk_id, [])
            merged = self._merge_entity_results(rule_ents, llm_ents)

            chunk_entities.append({
                "chunk_id": chunk.chunk_id,
                "content": chunk.content,
                "entities": merged
            })
            all_entities.extend(merged)

        # Stage 4: 去重
        entity_dict = {}
        for entity in all_entities:
            key = (entity.name.lower(), entity.type)
            if key not in entity_dict:
                entity_dict[key] = entity

        unique_entities = list(entity_dict.values())
        print(f"   Total unique entities: {len(unique_entities)}")

        # Stage 5: LLM 关系提取 (基于合并后的实体)
        all_relations = []
        if llm_service and chunk_entities:
            print(f"   Stage 3: LLM relation extraction (parallel)...")
            try:
                all_relations = await self._extract_relations_llm_parallel(chunks, chunk_entities, llm_service)
            except Exception as e:
                print(f"   Relation extraction failed: {e}")

        print(f"   Total relations extracted: {len(all_relations)}")

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
