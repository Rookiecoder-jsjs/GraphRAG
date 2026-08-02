"""DashScope (百炼) multimodal embedding provider — qwen3-vl-embedding.

Verified by scripts/probe_vl_embedding.py (2026-08-02): the OpenAI-compat
endpoint 404s for this model — only the NATIVE endpoint works. Text and
images embed into one shared vector space; every call pins
``parameters.dimension`` to EMBEDDING_DIM via MRL.
"""
import base64
from typing import List

from app.config import Settings
from app.services.providers._base import (
    EmbeddingServiceError,
    KIND_EMBEDDING,
    _HttpProviderBase,
    register_provider,
)


@register_provider(KIND_EMBEDDING, "dashscope")
class DashScopeEmbeddingProvider(_HttpProviderBase):
    """DashScope native multimodal endpoint (text + images)."""

    provider_name = "dashscope"
    supports_images = True

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self._embed_url = settings.DASHSCOPE_EMBEDDING_URL
        # Prefer the dedicated VL-embedding key (scoped quota); fall back
        # to the LLM's Bailian key when the dedicated one is absent so
        # single-key deployments keep working.
        self.api_key = (
            settings.BAILIAN_API_KEY_QWEN_VL_EMBEDDING or settings.BAILIAN_API_KEY
        )
        self.model = settings.DASHSCOPE_EMBEDDING_MODEL

    def _build_text_payload(self, texts: List[str]) -> dict:
        """Native multimodal shape, one ``{"text": t}`` content item per
        input; ``parameters.dimension`` pins the MRL output to
        EMBEDDING_DIM (probe T11, 2026-08-02)."""
        return {
            "model": self.model,
            "input": {"contents": [{"text": text} for text in texts]},
            "parameters": {"dimension": self.settings.EMBEDDING_DIM},
        }

    def _parse_embeddings(self, data: dict, expected: int) -> List[List[float]]:
        """Extract vectors from a native response, in input order.

        Raises:
            EmbeddingServiceError: on malformed payloads, a count mismatch,
                or dimension drift away from EMBEDDING_DIM (an MRL guard: a
                silently-wrong dim would corrupt the Chroma collection on
                upsert).
        """
        try:
            items = sorted(
                (data.get("output") or {}).get("embeddings") or [],
                key=lambda item: item.get("index", 0),
            )
            embeddings = [item["embedding"] for item in items]
        except (KeyError, TypeError) as e:
            raise EmbeddingServiceError(
                f"Malformed dashscope response: {e}"
            ) from e

        if len(embeddings) != expected:
            raise EmbeddingServiceError(
                f"dashscope returned {len(embeddings)} embeddings "
                f"for {expected} inputs"
            )
        for embedding in embeddings:
            if len(embedding) != self.settings.EMBEDDING_DIM:
                raise EmbeddingServiceError(
                    f"dashscope returned dim {len(embedding)}, expected "
                    f"{self.settings.EMBEDDING_DIM} — MRL drift, check "
                    "parameters.dimension"
                )
        return embeddings

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        data = await self._post_with_retry(
            self._embed_url, self._build_text_payload(texts), "DashScope"
        )
        return self._parse_embeddings(data, expected=len(texts))

    async def embed_image(self, image_bytes: bytes, media_type: str) -> List[float]:
        """Embed one image as a base64 data URI (single-image API)."""
        data_uri = (
            f"data:{media_type};base64,"
            f"{base64.b64encode(image_bytes).decode('ascii')}"
        )
        payload = {
            "model": self.model,
            "input": {"contents": [{"image": data_uri}]},
            "parameters": {"dimension": self.settings.EMBEDDING_DIM},
        }
        data = await self._post_with_retry(self._embed_url, payload, "DashScope")
        return self._parse_embeddings(data, expected=1)[0]
