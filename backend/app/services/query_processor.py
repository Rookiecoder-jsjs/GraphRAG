"""Query preprocessing service for RAG optimization."""
import re
import json
from typing import List, Dict, Any, Optional
from app.prompts import load_prompt
from app.services.llm import get_llm_service


class QueryProcessor:
    """Query preprocessing and enhancement service."""

    async def rewrite_query(
        self,
        query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> str:
        """Rewrite query to improve retrieval quality.

        When ``conversation_history`` is supplied (multi-turn chat), the
        rewrite also resolves pronouns/context into a STANDALONE query so
        retrieval isn't run against a bare "its performance?" that won't
        match any chunk. Single-turn callers (e.g. /api/search) pass None.
        """
        llm = await get_llm_service()

        history_block = ""
        standalone_instruction = ""
        if conversation_history:
            from app.config import get_settings
            n_turns = get_settings().CONVERSATIONAL_REWRITE_HISTORY_TURNS
            turns = conversation_history[-n_turns:] if n_turns > 0 else []
            if turns:
                history_block = "\n\nConversation so far:\n" + "\n".join(
                    f"{t.get('role', 'user')}: {t.get('content', '')}" for t in turns
                )
                standalone_instruction = (
                    "\n- The query is the latest turn in a conversation. Rewrite "
                    "it into a STANDALONE search query that resolves pronouns and "
                    "references using the conversation context."
                )

        prompt = load_prompt(
            "query_rewrite",
            query=query,
            standalone_instruction=standalone_instruction,
            history_block=history_block,
        )

        try:
            rewritten = await llm.chat_complete(
                [{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200,
                enable_thinking=False,
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

        prompt = load_prompt(
            "query_variants",
            num_variants=num_variants,
            query=query,
        )

        try:
            response = await llm.chat_complete(
                [{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=500,
                enable_thinking=False,
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
                max_tokens=300,
                enable_thinking=False,
            )

            entities = self._extract_json_array(response)
            return self._normalize_entities(entities)
        except Exception:
            return []

    @staticmethod
    def _normalize_entities(raw: Any) -> List[Dict[str, str]]:
        """Coerce whatever the LLM returned into ``[{"name", "type"}]`` dicts.

        Models drift between shapes: proper objects, bare strings, junk.
        Bare strings become name-only entities; anything without a usable
        name is dropped. Without this, a shape drift surfaced as an
        AttributeError three layers downstream in the graph channel
        (retriever's ``e.get("name")`` on a str) and every affected search
        answered 500 — found by the mock-provider stress run.
        """
        if not isinstance(raw, list):
            return []
        out: List[Dict[str, str]] = []
        for item in raw:
            if isinstance(item, str):
                name = item.strip()
                if name:
                    out.append({"name": name, "type": "CONCEPT"})
            elif isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    out.append({
                        "name": name,
                        "type": str(item.get("type") or "CONCEPT"),
                    })
        return out

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


# Singleton instance
_query_processor: Optional[QueryProcessor] = None


async def get_query_processor() -> QueryProcessor:
    """Get singleton query processor instance."""
    global _query_processor
    if _query_processor is None:
        _query_processor = QueryProcessor()
    return _query_processor