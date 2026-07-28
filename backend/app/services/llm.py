"""Bailian (百炼) LLM service."""
import asyncio
import logging
import re
import httpx
from typing import AsyncGenerator, List, Dict, Any, Optional, Tuple
import json

from app.config import get_settings

logger = logging.getLogger(__name__)


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
        model: Optional[str] = None,
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

        # Resolve the model from config (settings.BAILIAN_MODEL) when the
        # caller doesn't pin one. This is the single source of truth; the old
        # literal default in the signature silently overrode the config.
        model = model or self.default_model

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
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        enable_thinking: Optional[bool] = None,
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """
        Stream a chat completion.

        Args:
            messages: List of message dicts
            model: Model to use
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            enable_thinking: Qwen hybrid-thinking toggle (DashScope
                ``enable_thinking``). None = omit the param (legacy
                behavior); True = stream the model's reasoning before the
                answer; False = answer directly (faster first token).
                Non-thinking model tiers reject the param with HTTP 400 —
                handled below by dropping it and retrying once, so a stale
                toggle degrades to a normal answer instead of an error
                bubble.

        Yields:
            (kind, text) tuples where kind is "content" (answer body) or
            "thinking" (reasoning stream — only when thinking is enabled
            and the model supports it).
        """
        client = await self._get_client()

        # Resolve the model from config (settings.BAILIAN_MODEL) when the
        # caller doesn't pin one. This is the single source of truth; the old
        # literal default in the signature silently overrode the config.
        model = model or self.default_model

        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if enable_thinking is not None:
            payload["enable_thinking"] = enable_thinking

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async for item in self._stream_completions(client, url, headers, payload):
                yield item
        except httpx.HTTPStatusError as e:
            if (
                enable_thinking is not None
                and e.response is not None
                and e.response.status_code == 400
            ):
                # Non-thinking model tier: the provider rejects the
                # enable_thinking param. Drop it and retry ONCE so the user
                # gets a normal answer rather than an error bubble. Nothing
                # was yielded before raise_for_status fired, so the retry is
                # transparent to the caller.
                logger.warning(
                    "enable_thinking=%s rejected by provider (HTTP 400: %.200s); "
                    "retrying without it",
                    enable_thinking, e.response.text,
                )
                payload.pop("enable_thinking", None)
                try:
                    async for item in self._stream_completions(client, url, headers, payload):
                        yield item
                except Exception as retry_error:
                    yield ("content", f"\n[Error: {str(retry_error)}]")
            else:
                yield ("content", f"\n[Error: {str(e)}]")
        except Exception as e:
            yield ("content", f"\n[Error: {str(e)}]")

    async def _stream_completions(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """Open one SSE stream and yield (kind, text) delta tuples.

        kind is "thinking" for the ``reasoning_content`` field (Qwen hybrid
        thinking) and "content" for the answer body. Raises on HTTP errors
        so the caller can decide whether a param-rejection retry applies.
        """
        async with client.stream("POST", url, headers=headers, json=payload) as response:
            response.raise_for_status()

            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        # Providers send a trailing usage-only frame with an
                        # EMPTY choices list; guard before indexing [0] or
                        # we IndexError (which the outer handler would
                        # inject into the answer as "[Error: ...]").
                        choices = data.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        # Qwen hybrid thinking emits the reasoning stream in
                        # `reasoning_content` BEFORE the answer body starts.
                        # Forward it under the "thinking" kind so callers can
                        # route it to a separate UI block and keep the main
                        # first-token metric anchored to the answer body.
                        reasoning = delta.get("reasoning_content")
                        if reasoning:
                            yield ("thinking", reasoning)
                        # OpenAI-compatible providers also emit deltas like
                        # {"content": null} (role-only / final frames). The
                        # key is present but the value is None — yielding
                        # that produced `{"chunk": null}` and later crashed
                        # the "".join() in the caller. Only forward real,
                        # non-empty text.
                        content = delta.get("content")
                        if content:
                            yield ("content", content)
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

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

        # Limit concurrent requests to avoid 429 (config-tuned; the old
        # hard-coded 50 tripped rate limits whose backoffs slowed the run).
        semaphore = asyncio.Semaphore(self.settings.LLM_EXTRACTION_CONCURRENCY)

        async def _extract_single(text: str) -> List[Dict[str, Any]]:
            """Extract entities from a single text."""
            async with semaphore:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Extract entities from:\n\n{text[:2000]}"}
                ]
                for attempt in range(3):
                    try:
                        response = await self.chat_complete(
                            messages, temperature=0.1,
                            max_tokens=self.settings.LLM_EXTRACT_MAX_TOKENS,
                        )
                        json_match = self._extract_json(response)
                        if json_match:
                            entities = json.loads(json_match)
                            return entities if isinstance(entities, list) else []
                        return []
                    except Exception as e:
                        if "429" in str(e) and attempt < 2:
                            wait_time = (attempt + 1) * 2
                            logger.warning("[LLM Rate Limit] Retrying in %ds...", wait_time)
                            await asyncio.sleep(wait_time)
                            continue
                        logger.warning("[LLM Entity Extract Error] %s", e)
                        return []

        # Process all texts concurrently (bounded by the semaphore above).
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

        # Limit concurrent requests (config-tuned, see extract_entities_batch).
        semaphore = asyncio.Semaphore(self.settings.LLM_EXTRACTION_CONCURRENCY)

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
                        response = await self.chat_complete(
                            messages, temperature=0.1,
                            max_tokens=self.settings.LLM_EXTRACT_MAX_TOKENS,
                        )
                        json_match = self._extract_json(response)
                        if json_match:
                            relations = json.loads(json_match)
                            return relations if isinstance(relations, list) else []
                        return []
                    except Exception as e:
                        if "429" in str(e) and attempt < 2:
                            wait_time = (attempt + 1) * 2
                            logger.warning("[LLM Rate Limit] Retrying in %ds...", wait_time)
                            await asyncio.sleep(wait_time)
                            continue
                        logger.warning("[LLM Relation Extract Error] %s", e)
                        return []

        # Process concurrently
        tasks = [_extract_single(text, entities) for text, entities in zip(texts, entities_list)]
        results = await asyncio.gather(*tasks)
        return list(results)

    async def extract_entities_and_relations_batch(
        self,
        texts: List[str],
        entity_types: List[str] = None,
    ) -> List[Dict[str, List[Dict[str, Any]]]]:
        """Extract entities AND relations from multiple texts, ONE LLM call per text.

        Replaces the old two-stage pipeline (extract_entities_batch, then a
        full stage barrier, then extract_relations_batch): that design paid
        2N round-trips for N texts, and no relation call could start until
        EVERY chunk's entities had come back. Here each text makes a single
        call returning both, so LLM work is halved and fully overlapped.

        Args:
            texts: List of texts to process.
            entity_types: Allowed entity types named in the prompt.

        Returns:
            One dict per input text (same order, same length):
                {"entities":  [{"name","type","description"}, ...],
                 "relations": [{"source","target","relation_type"}, ...]}
            A failed or unparseable text yields empty arrays — extraction is
            best-effort and must never take down the whole document.
        """
        if entity_types is None:
            entity_types = ["PERSON", "ORGANIZATION", "LOCATION", "CONCEPT", "EVENT"]

        system_prompt = f"""You are a knowledge extraction assistant. Extract entities AND the relations between them from the given text.
Return ONLY a JSON object with exactly this shape:
{{"entities": [{{"name": "entity name", "type": "one of {', '.join(entity_types)}", "description": "brief description"}}],
 "relations": [{{"source": "entity name", "target": "entity name", "relation_type": "short relation label"}}]}}
Rules:
- Every relation's "source" and "target" MUST be names that appear in the "entities" array.
- If nothing is found, return {{"entities": [], "relations": []}}."""

        semaphore = asyncio.Semaphore(self.settings.LLM_EXTRACTION_CONCURRENCY)

        async def _extract_single(text: str) -> Dict[str, List[Dict[str, Any]]]:
            async with semaphore:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Extract entities and relations from:\n\n{text[:2000]}"},
                ]
                for attempt in range(3):
                    try:
                        response = await self.chat_complete(
                            messages, temperature=0.1,
                            max_tokens=self.settings.LLM_EXTRACT_MAX_TOKENS,
                        )
                        return self._parse_extraction_response(response)
                    except Exception as e:
                        if "429" in str(e) and attempt < 2:
                            wait_time = (attempt + 1) * 2
                            logger.warning("[LLM Rate Limit] Retrying in %ds...", wait_time)
                            await asyncio.sleep(wait_time)
                            continue
                        logger.warning("[LLM Combined Extract Error] %s", e)
                        return {"entities": [], "relations": []}
                return {"entities": [], "relations": []}

        tasks = [_extract_single(text) for text in texts]
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

    def _extract_json_object(self, text: str) -> Optional[str]:
        """Extract the first JSON object ({...}) from text.

        Greedy match to the LAST '}' — correct because the extraction
        prompts demand "Return ONLY a JSON object", so the object is the
        whole payload and there is no trailing prose whose braces could
        over-extend the match.
        """
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            return match.group(0)
        return None

    def _parse_extraction_response(
        self, text: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Normalise a combined entity+relation LLM response.

        Accepts the expected object shape {"entities": [...], "relations":
        [...]} and, as a fallback, a bare entity array (older prompt shape
        some models revert to). Always returns both keys with list values;
        entries that are not dicts or lack the required name / source+target
        fields are dropped, so downstream code never has to re-validate.
        """
        empty: Dict[str, List[Dict[str, Any]]] = {"entities": [], "relations": []}

        # Pick the parse path by whichever structural token appears first:
        # '{' → expected object shape; '[' → legacy bare-entity-array shape.
        # Position matters — blindly trying the object regex first would
        # hijack the INNER {...} of a bare array and discard real entities.
        first_obj = text.find("{")
        first_arr = text.find("[")

        if first_obj != -1 and (first_arr == -1 or first_obj < first_arr):
            obj_str = self._extract_json_object(text)
            if obj_str:
                try:
                    parsed = json.loads(obj_str)
                except (json.JSONDecodeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    entities = parsed.get("entities")
                    relations = parsed.get("relations")
                    return {
                        "entities": [
                            e for e in entities
                            if isinstance(e, dict) and str(e.get("name") or "").strip()
                        ] if isinstance(entities, list) else [],
                        "relations": [
                            r for r in relations
                            if isinstance(r, dict)
                            and str(r.get("source") or "").strip()
                            and str(r.get("target") or "").strip()
                        ] if isinstance(relations, list) else [],
                    }

        # Fallback: bare array → treat as entities-only rather than
        # discarding the work.
        arr_str = self._extract_json(text)
        if arr_str:
            try:
                parsed = json.loads(arr_str)
            except (json.JSONDecodeError, ValueError):
                return empty
            if isinstance(parsed, list):
                return {
                    "entities": [
                        e for e in parsed
                        if isinstance(e, dict) and str(e.get("name") or "").strip()
                    ],
                    "relations": [],
                }
        return empty

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


async def close_llm_service() -> None:
    """Close the shared LLM HTTP client at shutdown (no-op if never created)."""
    global _llm_service
    if _llm_service is not None:
        await _llm_service.close()
        _llm_service = None
