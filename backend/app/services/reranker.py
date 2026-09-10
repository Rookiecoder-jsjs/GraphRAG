"""Silicon Flow rerank service."""
import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import get_settings
from app.services.key_pool import get_key_pool, run_with_key_retry

logger = logging.getLogger(__name__)


class RerankService:
    """Service for reranking documents using Silicon Flow API."""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.SILICON_FLOW_BASE_URL
        # Shares the SiliconFlow multi-key pool with the embedding service
        # (ADR-009): least-inflight keys, 429 cooldown + immediate failover.
        self._pool = get_key_pool()
        self.model = self.settings.RERANK_MODEL
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0)
        return self._client

    async def rerank(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """Rerank chunks by relevance to query.

        Retries transient provider failures (5xx / 429 / transport) through
        the shared key pool: a 429 cools the key down and fails over to an
        idle one without sleeping. On final failure (including pool
        exhaustion after a bounded wait) falls back to the input order so
        the chat/search request still gets results — the fallback is logged
        as a warning (it silently degraded retrieval quality before).
        """
        if not chunks:
            return []

        client = await self._get_client()
        url = f"{self.base_url}/rerank"
        documents = [chunk["content"] for chunk in chunks]

        async def post_once(key: str) -> Dict[str, Any]:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {key}"},
                json={
                    "model": self.model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_k,
                },
            )
            response.raise_for_status()
            return response.json()

        try:
            data = await run_with_key_retry(
                "rerank", self._pool, post_once, max_attempts=3,
            )
        except Exception as e:
            # Fallback to original order on error — no scores available
            # since the API never responded. Callers (chat) treat a
            # missing score as "unknown quality" (rendered as medium).
            logger.warning(
                "rerank failed after retries, falling back to input order: %s", e,
            )
            return chunks[:top_k]

        # Map reranked results back to original chunks, attaching
        # the relevance score so the chat layer can show a quality
        # badge to the user (e.g. "[1] high" vs "[2] low"). We
        # accept either `relevance_score` (siliconflow default) or
        # `score` (jina / cohere style) — different vendors name
        # the field differently, but they're both 0..1 floats.
        reranked = []
        for result in data["results"][:top_k]:
            idx = result["index"]
            chunk = dict(chunks[idx])  # shallow copy so we don't
                                       # mutate the caller's chunk
            score = result.get("relevance_score", result.get("score"))
            if score is not None:
                chunk["relevance_score"] = float(score)
            reranked.append(chunk)

        return reranked

    async def close(self):
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None


# Singleton instance
_rerank_service: Optional[RerankService] = None


async def get_rerank_service() -> RerankService:
    """Get singleton rerank service instance."""
    global _rerank_service
    if _rerank_service is None:
        _rerank_service = RerankService()
    return _rerank_service


async def close_rerank_service() -> None:
    """Close the shared rerank HTTP client at shutdown (no-op if never created)."""
    global _rerank_service
    if _rerank_service is not None:
        await _rerank_service.close()
        _rerank_service = None
