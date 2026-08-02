"""SiliconFlow reranker provider — text-only Qwen3-Reranker-8B."""
from typing import Any, Dict, List

import httpx

from app.config import Settings
from app.services.providers._base import KIND_RERANKER, _HttpProviderBase, register_provider


@register_provider(KIND_RERANKER, "siliconflow")
class SiliconFlowRerankerProvider(_HttpProviderBase):
    """POST /rerank against the SiliconFlow endpoint."""

    provider_name = "siliconflow"

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.base_url = settings.SILICON_FLOW_BASE_URL
        self.api_key = settings.SILICON_FLOW_API_KEY
        self.model = settings.RERANK_MODEL

    async def rerank(
        self,
        query: str,
        documents: List[str],
        top_n: int,
    ) -> List[Dict[str, Any]]:
        """Score documents against the query.

        Returns provider-ordered ``[{"index": int, "score": float|None}]``
        where ``index`` is the position in ``documents``. Accepts either
        ``relevance_score`` (siliconflow default) or ``score`` (jina /
        cohere style) — different vendors name the field differently, but
        they're both 0..1 floats.

        Raises httpx.HTTPError on transport/HTTP failure — the service
        shell decides the fallback.
        """
        client = await self._get_client()
        response = await client.post(
            f"{self.base_url}/rerank",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": top_n,
            },
        )
        response.raise_for_status()
        data = response.json()
        return [
            {
                "index": result["index"],
                "score": result.get("relevance_score", result.get("score")),
            }
            for result in data.get("results", [])
        ]
