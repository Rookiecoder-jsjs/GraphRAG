"""Silicon Flow rerank service."""
import httpx
from typing import List, Dict, Any, Optional

from app.config import get_settings


class RerankService:
    """Service for reranking documents using Silicon Flow API."""

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.SILICON_FLOW_BASE_URL
        self.api_key = self.settings.SILICON_FLOW_API_KEY
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

        Modality split happens HERE so both call sites (search, chat) stay
        unchanged: the reranker model is text-only, so image chunks are
        partitioned out, keep their cosine rank order (RRF preserves the
        vector-lane rank for image-only entries), and are appended after
        the reranked texts under a fixed quota. Images carry no
        relevance_score — callers render a missing score as "medium".
        """
        if not chunks:
            return []

        quota = self.settings.IMAGE_RESULT_QUOTA
        images = [
            chunk for chunk in chunks
            if (chunk.get("metadata") or {}).get("modality") == "image"
        ][:quota]
        texts = [
            chunk for chunk in chunks
            if (chunk.get("metadata") or {}).get("modality") != "image"
        ]

        if not texts:
            return images

        documents = [chunk["content"] for chunk in texts]
        client = await self._get_client()

        try:
            response = await client.post(
                f"{self.base_url}/rerank",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "query": query,
                    "documents": documents,
                    "top_n": top_k
                }
            )
            response.raise_for_status()
            data = response.json()

            # Map reranked results back to original chunks, attaching
            # the relevance score so the chat layer can show a quality
            # badge to the user (e.g. "[1] high" vs "[2] low"). We
            # accept either `relevance_score` (siliconflow default) or
            # `score` (jina / cohere style) — different vendors name
            # the field differently, but they're both 0..1 floats.
            reranked = []
            for result in data["results"][:top_k]:
                idx = result["index"]
                chunk = dict(texts[idx])  # shallow copy so we don't
                                          # mutate the caller's chunk
                score = result.get("relevance_score", result.get("score"))
                if score is not None:
                    chunk["relevance_score"] = float(score)
                reranked.append(chunk)

            return reranked + images

        except httpx.HTTPError:
            # Fallback to original order on error — no scores available
            # since the API never responded. Callers (chat) treat a
            # missing score as "unknown quality" (rendered as medium).
            return texts[:top_k] + images

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
