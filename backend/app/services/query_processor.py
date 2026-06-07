"""Query preprocessing service for RAG optimization."""
import re
import json
from typing import List, Dict, Any, Optional
from app.services.llm import get_llm_service


class QueryProcessor:
    """Query preprocessing and enhancement service."""

    async def rewrite_query(self, query: str) -> str:
        """
        Rewrite query to improve retrieval quality.

        This expands abbreviations, clarifies intent, and makes
        the query more effective for semantic search.

        Args:
            query: Original user query

        Returns:
            Rewritten query
        """
        llm = await get_llm_service()

        prompt = f"""You are a query rewriting assistant for search retrieval.
Rewrite the following search query to improve retrieval quality.

Guidelines:
- Expand abbreviations and technical terms to full forms
- Make implicit concepts explicit
- Keep the original intent but express it more clearly
- Use more precise terminology where applicable
- Keep it concise (preferably under 100 characters)

Original query: "{query}"

Rewritten query (just return the rewritten query, nothing else):"""

        try:
            rewritten = await llm.chat_complete(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200
            )
            return rewritten.strip().strip('"').strip()
        except Exception as e:
            # Fallback to original query on error
            return query

    async def generate_query_variants(
        self,
        query: str,
        num_variants: int = 3
    ) -> List[str]:
        """
        Generate multiple semantic variations of the query.

        These variants capture different aspects and phrasings
        of the original query for multi-query retrieval.

        Args:
            query: Original user query
            num_variants: Number of variants to generate

        Returns:
            List of query variants
        """
        llm = await get_llm_service()

        prompt = f"""Generate {num_variants} different search query variations
that would help find relevant documents for answering the original query.

Guidelines:
- Vary the wording and phrasing
- Include synonyms and related terms
- Some can be more specific, some more general
- Include different question forms (what, how, why, etc.)

Original query: "{query}"

Return ONLY a JSON array of strings, nothing else. Example format:
["variant 1", "variant 2", "variant 3"]"""

        try:
            response = await llm.chat_complete(
                [{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=500
            )

            # Extract JSON array from response
            variants = self._extract_json_array(response)
            return variants[:num_variants]
        except Exception:
            return []

    async def extract_entities(self, query: str) -> List[Dict[str, str]]:
        """
        Extract named entities from query for graph-based filtering.

        Args:
            query: User query

        Returns:
            List of extracted entities with type
        """
        llm = await get_llm_service()

        prompt = f"""Extract named entities from the following query.
Return a JSON array of objects with "name" and "type" fields.

Entity types: PERSON, ORGANIZATION, LOCATION, CONCEPT, EVENT, TECHNOLOGY, DATE

Query: "{query}"

Return ONLY a JSON array, nothing else. Example:
[{{"name": "Python", "type": "TECHNOLOGY"}}, {{"name": "Google", "type": "ORGANIZATION"}}]

Entities:"""

        try:
            response = await llm.chat_complete(
                [{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=300
            )

            entities = self._extract_json_array(response)
            return entities if isinstance(entities, list) else []
        except Exception:
            return []

    async def expand_query(self, query: str) -> Dict[str, Any]:
        """
        Comprehensive query expansion combining all techniques.

        Args:
            query: Original user query

        Returns:
            Dict with rewritten query, variants, and entities
        """
        # Run expansions in parallel
        rewritten_task = self.rewrite_query(query)
        variants_task = self.generate_query_variants(query, num_variants=3)
        entities_task = self.extract_entities(query)

        # Gather results
        import asyncio
        rewritten, variants, entities = await asyncio.gather(
            rewritten_task,
            variants_task,
            entities_task
        )

        return {
            "original_query": query,
            "rewritten_query": rewritten,
            "variants": variants,
            "entities": entities,
            "all_queries": [rewritten] + variants if rewritten else variants
        }

    def _extract_json_array(self, text: str) -> List:
        """Extract JSON array from LLM response."""
        # Try to find JSON array
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return []

    def simple_tokenize(self, text: str) -> List[str]:
        """
        Simple query tokenization for keyword matching.

        Args:
            text: Query text

        Returns:
            List of tokens
        """
        # Extract alphanumeric and CJK characters
        tokens = re.findall(r'[\w\u4e00-\u9fff]+', text.lower())
        # Filter short tokens
        return [t for t in tokens if len(t) >= 2]


# Singleton instance
_query_processor: Optional[QueryProcessor] = None


async def get_query_processor() -> QueryProcessor:
    """Get singleton query processor instance."""
    global _query_processor
    if _query_processor is None:
        _query_processor = QueryProcessor()
    return _query_processor