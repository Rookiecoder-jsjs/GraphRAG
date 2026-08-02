"""Rerank service — thin shell delegating to the active reranker provider.

The provider registry (``app/services/providers/``) owns the wire call
(POST /rerank, score-field normalization); this shell owns the
cross-provider concerns: the modality split (images bypass the text-only
reranker and keep cosine order under a fixed quota) and the HTTP-failure
fallback to original order.
"""
from typing import List, Dict, Any, Optional

import httpx

from app.config import get_settings
from app.services.providers import KIND_RERANKER, get_provider_class


class RerankService:
    """Service for reranking documents — delegates to the active provider.

    Modality split happens HERE so both call sites (search, chat) stay
    unchanged: the reranker model is text-only, so image chunks are
    partitioned out, keep their cosine rank order (RRF preserves the
    vector-lane rank for image-only entries), and are appended after
    the reranked texts under a fixed quota. Images carry no
    relevance_score — callers render a missing score as "medium".
    """

    def __init__(self):
        self.settings = get_settings()
        provider_name = self.settings.RERANKER_PROVIDER.lower()
        self._provider = get_provider_class(KIND_RERANKER, provider_name)(
            self.settings
        )

    async def rerank(
        self,
        query: str,
        chunks: List[Dict[str, Any]],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """Rerank chunks by relevance to query."""
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
        try:
            results = await self._provider.rerank(query, documents, top_n=top_k)
        except httpx.HTTPError:
            # Fallback to original order on error — no scores available
            # since the API never responded. Callers (chat) treat a
            # missing score as "unknown quality" (rendered as medium).
            return texts[:top_k] + images

        # Map reranked results back to original chunks, attaching
        # the relevance score so the chat layer can show a quality
        # badge to the user (e.g. "[1] high" vs "[2] low").
        reranked = []
        for result in results[:top_k]:
            idx = result["index"]
            chunk = dict(texts[idx])  # shallow copy so we don't
                                      # mutate the caller's chunk
            score = result.get("score")
            if score is not None:
                chunk["relevance_score"] = float(score)
            reranked.append(chunk)

        return reranked + images

    async def close(self):
        """Close the provider's shared HTTP client (no-op if never created)."""
        await self._provider.close()


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
