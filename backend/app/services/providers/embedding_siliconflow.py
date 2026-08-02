"""SiliconFlow embedding provider — OpenAI-compatible /embeddings, text only."""
from typing import List

from app.config import Settings
from app.services.providers._base import (
    EmbeddingServiceError,
    KIND_EMBEDDING,
    _HttpProviderBase,
    register_provider,
)


@register_provider(KIND_EMBEDDING, "siliconflow")
class SiliconFlowEmbeddingProvider(_HttpProviderBase):
    """OpenAI-compatible ``POST /embeddings`` (text only)."""

    provider_name = "siliconflow"
    supports_images = False

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self._embed_url = f"{settings.SILICON_FLOW_BASE_URL}/embeddings"
        self.api_key = settings.SILICON_FLOW_API_KEY
        self.model = settings.EMBEDDING_MODEL

    def _build_text_payload(self, texts: List[str]) -> dict:
        """Single text goes out as a bare string, a batch as a list —
        byte-identical to the pre-provider-split wire format, so rollback
        behavior is unchanged. Deliberately does NOT pin `dimensions` —
        the model's native dim is what the existing collection was built
        with."""
        return {
            "model": self.model,
            "input": texts[0] if len(texts) == 1 else texts,
            "encoding_format": "float",
        }

    def _parse_embeddings(self, data: dict, expected: int) -> List[List[float]]:
        try:
            embeddings = [item["embedding"] for item in data["data"]]
        except (KeyError, TypeError) as e:
            raise EmbeddingServiceError(
                f"Malformed siliconflow response: {e}"
            ) from e
        if len(embeddings) != expected:
            raise EmbeddingServiceError(
                f"siliconflow returned {len(embeddings)} embeddings "
                f"for {expected} inputs"
            )
        return embeddings

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        data = await self._post_with_retry(
            self._embed_url, self._build_text_payload(texts), "SiliconFlow"
        )
        return self._parse_embeddings(data, expected=len(texts))

    async def embed_image(self, image_bytes: bytes, media_type: str) -> List[float]:
        # Images exist only in the multimodal vector space; a text-only
        # provider must refuse loudly instead of returning a zero vector
        # that would poison cross-modal retrieval.
        raise EmbeddingServiceError(
            "image embedding requires EMBEDDING_PROVIDER=dashscope "
            f"(active provider: {self.provider_name})"
        )
