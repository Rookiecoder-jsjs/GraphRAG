"""Bailian (百炼) LLM service."""
import asyncio
import re
import httpx
from typing import AsyncGenerator, List, Dict, Any, Optional
import json

from app.config import get_settings


class LLMService:
    """Service for interacting with Bailian (百炼) API."""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.BAILIAN_BASE_URL
        self.api_key = self.settings.BAILIAN_API_KEY
        self.default_model = self.settings.BAILIAN_MODEL
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=120.0,
                limits=httpx.Limits(max_connections=100, max_keepalive_connections=50)
            )
        return self._client

    async def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: str = "qwen-flash",
        temperature: float = 0.7,
        max_tokens: int = 8000,
        stream: bool = False
    ) -> str:
        """
        Complete a chat conversation.

        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model to use
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            stream: Whether to stream response

        Returns:
            Generated response text
        """
        client = await self._get_client()

        if not self.api_key:
            raise ValueError("No API key configured")

        # Use default model if not specified
        if model == "kimi-k2-0905-preview":
            model = self.default_model

        try:
            response = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens
                }
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

        except httpx.HTTPError as e:
            # Retry with delay
            await asyncio.sleep(1)
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": temperature,
                        "max_tokens": max_tokens
                    }
                )
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except Exception as retry_error:
                raise

    async def chat_complete_stream(
        self,
        messages: List[Dict[str, str]],
        model: str = "qwen3.5-flash",
        temperature: float = 0.7,
        max_tokens: int = 2000
    ) -> AsyncGenerator[str, None]:
        """
        Stream a chat completion.

        Args:
            messages: List of message dicts
            model: Model to use
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate

        Yields:
            Chunks of the generated response
        """
        client = await self._get_client()

        # Use default model if not specified
        if model == "kimi-k2-0905-preview":
            model = self.default_model

        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "stream": True
                }
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data.get("choices", [{}])[0].get("delta", {})
                            if "content" in delta:
                                yield delta["content"]
                        except (json.JSONDecodeError, KeyError):
                            continue

        except Exception as e:
            yield f"\n[Error: {str(e)}]"

    async def extract_entities_batch(
        self,
        texts: List[str],
        entity_types: List[str] = None
    ) -> List[List[Dict[str, Any]]]:
        """
        Extract entities from multiple texts using LLM.

        Args:
            texts: List of texts to process
            entity_types: Types of entities to extract

        Returns:
            List of entity lists for each text
        """
        if entity_types is None:
            entity_types = ["PERSON", "ORGANIZATION", "LOCATION", "CONCEPT", "EVENT"]

        system_prompt = f"""You are an entity extraction assistant. Extract entities from the given text.
Return ONLY a JSON array of objects with format: {{"name": "entity name", "type": "one of {', '.join(entity_types)}", "description": "brief description"}}.
If no entities are found, return an empty array."""

        # Limit concurrent requests to avoid 429
        semaphore = asyncio.Semaphore(50)

        async def _extract_single(text: str) -> List[Dict[str, Any]]:
            """Extract entities from a single text."""
            async with semaphore:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Extract entities from:\n\n{text[:2000]}"}
                ]
                for attempt in range(3):
                    try:
                        response = await self.chat_complete(messages, temperature=0.1)
                        json_match = self._extract_json(response)
                        if json_match:
                            entities = json.loads(json_match)
                            return entities if isinstance(entities, list) else []
                        return []
                    except Exception as e:
                        if "429" in str(e) and attempt < 2:
                            wait_time = (attempt + 1) * 2
                            print(f"   [LLM Rate Limit] Retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                        print(f"   [LLM Entity Extract Error] {e}")
                        return []

        # Process all texts concurrently (with semaphore limiting to 5)
        tasks = [_extract_single(text) for text in texts]
        results = await asyncio.gather(*tasks)
        return list(results)

    async def extract_relations_batch(
        self,
        texts: List[str],
        entities_list: List[List[Dict[str, Any]]]
    ) -> List[List[Dict[str, Any]]]:
        """
        Extract relations between entities from texts.

        Args:
            texts: List of texts
            entities_list: List of entity lists for each text

        Returns:
            List of relation lists for each text
        """
        system_prompt = """You are a relation extraction assistant. Identify relationships between the given entities.
Return ONLY a JSON array of objects with format: {"source": "entity name", "target": "entity name", "relation_type": "relationship type"}.
If no relations are found, return an empty array."""

        # Limit concurrent requests
        semaphore = asyncio.Semaphore(50)

        async def _extract_single(text: str, entities: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            async with semaphore:
                if len(entities) < 2:
                    return []

                entity_names = [e["name"] for e in entities]
                prompt = f"""Given these entities: {', '.join(entity_names)}

And this text:
{text[:1500]}

Extract relationships between the entities."""

                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ]

                for attempt in range(3):
                    try:
                        response = await self.chat_complete(messages, temperature=0.1)
                        json_match = self._extract_json(response)
                        if json_match:
                            relations = json.loads(json_match)
                            return relations if isinstance(relations, list) else []
                        return []
                    except Exception as e:
                        if "429" in str(e) and attempt < 2:
                            wait_time = (attempt + 1) * 2
                            print(f"   [LLM Rate Limit] Retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                            continue
                        print(f"   [LLM Relation Extract Error] {e}")
                        return []

        # Process concurrently
        tasks = [_extract_single(text, entities) for text, entities in zip(texts, entities_list)]
        results = await asyncio.gather(*tasks)
        return list(results)

    async def generate_rag_response(
        self,
        query: str,
        context_chunks: List[Dict[str, Any]],
        related_entities: List[Dict[str, Any]] = None,
        related_relations: List[Dict[str, Any]] = None,
        conversation_history: List[Dict[str, str]] = None,
        custom_context_str: Optional[str] = None,
        citation_instruction: Optional[str] = None,
        comparison_mode: bool = False,
    ) -> str:
        """
        Generate a RAG response based on retrieved context.

        Args:
            query: User query
            context_chunks: Retrieved document chunks
            related_entities: Related entities from graph
            related_relations: Related relations from graph
            conversation_history: Previous conversation messages
            custom_context_str: When provided, used verbatim as the
                "Context" block in the prompt (the caller is responsible
                for any numbering/citation markers). If None, we build a
                default "[Document N] Path: ..." context from context_chunks.
            citation_instruction: When provided, appended to the system
                prompt (e.g. instructions telling the LLM to emit [N]
                markers). No-op when None.
            comparison_mode: When True, append a COMPARISON instruction
                that asks the LLM to structure the answer to highlight
                cross-document agreements/disagreements. The caller is
                expected to have already formatted the context with
                document titles inline (so the LLM can identify which
                source each claim came from).

        Returns:
            Generated response
        """
        # Build context string. If the caller supplied one, trust it; that
        # is what gives us guaranteed alignment between the prompt and the
        # citation index sent back to the client.
        if custom_context_str is not None:
            context_str = custom_context_str
        else:
            context_parts = []
            for i, chunk in enumerate(context_chunks[:5], 1):
                hierarchy = chunk.get("hierarchy", {})
                path = hierarchy.get("path", [])
                context_parts.append(f"[Document {i}] Path: {' > '.join(path)}\n{chunk.get('content', '')}")
            context_str = "\n\n".join(context_parts)

        # Add graph context if available
        graph_context = ""
        if related_entities:
            entity_str = ", ".join([e.get("name", "") for e in related_entities[:10]])
            graph_context += f"\n\nRelated Entities: {entity_str}"

        citation_block = f"\n\n{citation_instruction}" if citation_instruction else ""
        comparison_block = (
            "\n\nCOMPARISON MODE: The user is asking you to compare or contrast "
            "information across multiple sources. For each claim, lead with the "
            "source document name (e.g. \"According to <Doc A>, ...\"). Make the "
            "comparison explicit: when sources agree, say so; when they disagree, "
            "highlight the difference. Use [N] citations alongside the document "
            "references so the user can click through to the original chunks."
            if comparison_mode else ""
        )

        system_prompt = f"""You are a helpful assistant answering questions based on the provided documents and knowledge graph.
Use ONLY the information from the provided context. If the answer is not in the context, say so clearly.

Context:
{context_str}
{graph_context}{citation_block}{comparison_block}"""

        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history
        if conversation_history:
            messages.extend(conversation_history[-5:])  # Last 5 messages

        messages.append({"role": "user", "content": query})

        return await self.chat_complete(messages, temperature=0.7, max_tokens=8000)

    def _extract_json(self, text: str) -> Optional[str]:
        """Extract JSON array from text."""
        # Try to find JSON array
        match = re.search(r'\[.*\]', text, re.DOTALL)
        if match:
            return match.group(0)
        return None

    async def generate_followups(
        self,
        query: str,
        answer: str,
        n: int = 3,
    ) -> List[str]:
        """Generate up to `n` follow-up question chips based on the just-
        given answer. The returned list is capped at n, blank entries
        are dropped, and the call NEVER raises — a failure here is a
        UX bonus, not a blocking dependency.

        Returns an empty list if the LLM call fails, returns non-JSON,
        or returns a list that has no usable strings after cleaning.
        """
        system_prompt = (
            "You are a follow-up question generator. Given a user's "
            "question and the assistant's answer, suggest the next "
            f"{n} questions the user is most likely to ask to go "
            "deeper. Output ONLY a JSON array of strings — no prose, "
            "no markdown fences, no numbering. Each entry should be a "
            "complete, natural-language question. Example shape: "
            '["How does X relate to Y?", "What about Z?", "Why does W?"]'
        )
        user_prompt = (
            f"User question:\n{query}\n\n"
            f"Assistant answer:\n{(answer or '')[:2000]}\n\n"
            f"Return the {n} follow-up questions."
        )
        try:
            raw = await self.chat_complete(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.7,
                max_tokens=400,
            )
        except Exception as e:
            print(f"   [LLM Followups Error] {e}")
            return []

        # The LLM may have wrapped the array in ```json … ``` fences or
        # a prose preamble — `_extract_json` finds the first JSON array
        # in the text. If there's none at all we bail out.
        json_str = self._extract_json(raw or "")
        if not json_str:
            return []
        try:
            parsed = json.loads(json_str)
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(parsed, list):
            return []

        # Cap at n, drop non-strings, strip whitespace, drop empties.
        cleaned: List[str] = []
        for item in parsed:
            if not isinstance(item, str):
                continue
            s = item.strip()
            if s:
                cleaned.append(s)
            if len(cleaned) >= n:
                break
        return cleaned

    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None


# Singleton instance
_llm_service: Optional[LLMService] = None


async def get_llm_service() -> LLMService:
    """Get singleton LLM service instance."""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
