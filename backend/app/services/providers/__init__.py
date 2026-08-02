"""Provider strategy registry for embedding / LLM / reranking.

Importing this package registers every built-in provider (the submodule
imports below run their ``@register_provider`` decorators); the service
shells look up the active one via ``get_provider_class`` at construction.
"""
from app.services.providers._base import (
    EmbeddingProvider,
    EmbeddingServiceError,
    KIND_EMBEDDING,
    KIND_LLM,
    KIND_RERANKER,
    LLMProvider,
    MAX_ATTEMPTS,
    RETRY_DELAYS_SECONDS,
    RerankerProvider,
    get_provider_class,
    register_provider,
)

# Import submodules so their @register_provider decorators run at package
# import. Order matters only for readability — registration is keyed.
from app.services.providers import (  # noqa: F401,E402
    embedding_dashscope,
    embedding_siliconflow,
    llm_bailian,
    reranker_siliconflow,
)

__all__ = [
    "EmbeddingProvider",
    "EmbeddingServiceError",
    "KIND_EMBEDDING",
    "KIND_LLM",
    "KIND_RERANKER",
    "LLMProvider",
    "MAX_ATTEMPTS",
    "RETRY_DELAYS_SECONDS",
    "RerankerProvider",
    "get_provider_class",
    "register_provider",
]
