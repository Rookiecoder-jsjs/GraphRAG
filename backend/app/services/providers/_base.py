"""Provider protocols and the strategy registry.

A provider is a self-contained implementation of one capability
(embedding / LLM / reranking) for one vendor. The services
(``app/services/embedding.py``, ``llm.py``, ``reranker.py``) are thin
shells: they own the cross-provider concerns (SQLite cache, concurrency
semaphore, prompt building, modality handling) and delegate the
vendor-specific wire work to the active provider.

Adding a new provider = implement the matching Protocol in a new module
+ one ``@register_provider`` line. No service code changes.

Providers are constructed as ``cls(settings)`` by the service shell;
``settings`` is the resolved ``Settings`` instance (the shell's
``get_settings()`` result — a test may substitute a fake).
"""
import asyncio
import logging
from typing import (
    Any,
    AsyncGenerator,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
    Type,
)

import httpx

logger = logging.getLogger(__name__)

KIND_EMBEDDING = "embedding"
KIND_LLM = "llm"
KIND_RERANKER = "reranker"


class EmbeddingServiceError(Exception):
    """Raised when the embedding service cannot produce a vector after all retries."""


# Retry policy: only transport / 5xx errors are retried; 4xx (auth, bad request)
# will not fix themselves and should fail fast.
RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
RETRYABLE_EXCEPTIONS = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.LocalProtocolError,
)
MAX_ATTEMPTS = 5
RETRY_DELAYS_SECONDS = [1, 2, 4, 8, 16]


class EmbeddingProvider(Protocol):
    """Wire contract for an embedding vendor.

    The shell owns caching, batching, the concurrency semaphore, and
    zero-vector handling for blank text; the provider owns the HTTP call
    (URL, auth, payload shape, response parsing, retry policy).
    """

    provider_name: str
    model: str
    supports_images: bool

    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """Embed N texts → N vectors in input order.

        Raises EmbeddingServiceError on malformed responses, count/dimension
        drift, or unrecoverable API failure.
        """
        ...

    async def embed_image(self, image_bytes: bytes, media_type: str) -> List[float]:
        """Embed one image → one vector.

        Providers without image support raise EmbeddingServiceError loudly —
        a zero vector would silently poison cross-modal retrieval.
        """
        ...

    async def close(self) -> None:
        """Close any HTTP resources (no-op if never created)."""
        ...


class LLMProvider(Protocol):
    """Wire contract for a chat-completion vendor."""

    provider_name: str
    base_url: str
    api_key: str
    default_model: str

    async def chat_complete(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 8000,
        stream: bool = False,
    ) -> str:
        """Complete a chat conversation and return the assistant text."""
        ...

    async def chat_complete_stream(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        enable_thinking: Optional[bool] = None,
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """Stream a chat completion as (kind, text) tuples.

        kind is "content" (answer body) or "thinking" (reasoning stream).
        """
        ...

    async def close(self) -> None:
        """Close any HTTP resources (no-op if never created)."""
        ...


class RerankerProvider(Protocol):
    """Wire contract for a document-reranking vendor."""

    provider_name: str
    base_url: str
    api_key: str
    model: str

    async def rerank(
        self,
        query: str,
        documents: List[str],
        top_n: int,
    ) -> List[Dict[str, Any]]:
        """Score documents against the query.

        Returns provider-ordered ``[{"index": int, "score": float|None}]``
        where ``index`` is the position in ``documents``. Raises
        ``httpx.HTTPError`` on transport/HTTP failure — the shell decides
        the fallback.
        """
        ...

    async def close(self) -> None:
        """Close any HTTP resources (no-op if never created)."""
        ...


_registry: Dict[str, Dict[str, Type[Any]]] = {
    KIND_EMBEDDING: {},
    KIND_LLM: {},
    KIND_RERANKER: {},
}


def register_provider(kind: str, name: str):
    """Class decorator: register a provider class under (kind, name).

    The class is instantiated as ``cls(settings)`` by the service shell.
    Duplicate registration raises — a second provider claiming a name
    would silently shadow the first.
    """

    def deco(cls):
        existing = _registry[kind].get(name)
        if existing is not None and existing is not cls:
            raise ValueError(f"Duplicate {kind} provider {name!r}")
        _registry[kind][name] = cls
        return cls

    return deco


def get_provider_class(kind: str, name: str) -> Type[Any]:
    """Look up a registered provider class, failing loudly on typos.

    A typo here (e.g. "dash_scope") would otherwise silently land on the
    wrong provider — or mix vector spaces after a partial switch. The
    error lists known providers for the kind, mirroring the startup
    validation in config.get_settings().
    """
    try:
        return _registry[kind][name]
    except KeyError:
        raise ValueError(
            f"Unknown {kind} provider {name!r}; registered: {sorted(_registry[kind])}"
        ) from None


class _HttpProviderBase:
    """Shared HTTP plumbing for providers: keep-alive client + backoff.

    Subclasses must set ``self.api_key`` in __init__ (used for the
    Authorization header) and may override the class-attr client tuning.
    """

    REQUEST_TIMEOUT_SECONDS = 60.0
    MAX_CONNECTIONS = 20
    MAX_KEEPALIVE_CONNECTIONS = 10

    def __init__(self) -> None:
        self._client: Optional[httpx.AsyncClient] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared keep-alive HTTP client.

        Reusing one client keeps the TLS connection warm across calls —
        the old per-attempt client paid a fresh TCP+TLS handshake
        (~100–300ms) on EVERY request, which dominated latency.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.REQUEST_TIMEOUT_SECONDS,
                limits=httpx.Limits(
                    max_connections=self.MAX_CONNECTIONS,
                    max_keepalive_connections=self.MAX_KEEPALIVE_CONNECTIONS,
                ),
            )
        return self._client

    async def close(self) -> None:
        """Close the shared HTTP client (no-op if never created)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _post_with_retry(self, url: str, payload: dict, label: str) -> dict:
        """POST JSON with exponential backoff on transport / 5xx errors.

        Args:
            url: Provider endpoint.
            payload: JSON request body.
            label: Provider display name used in error/log text
                ("DashScope", "SiliconFlow").

        Raises:
            EmbeddingServiceError: after MAX_ATTEMPTS exhausted on retryable
                error or immediately on a 4xx response.
        """
        last_error: Optional[BaseException] = None
        headers = {"Authorization": f"Bearer {self.api_key}"}
        client = await self._get_client()

        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await client.post(url, headers=headers, json=payload)
            except RETRYABLE_EXCEPTIONS as e:
                last_error = e
                if attempt < MAX_ATTEMPTS - 1:
                    delay = RETRY_DELAYS_SECONDS[attempt]
                    logger.warning(
                        "Embedding call %d/%d transport error: %s — retrying in %ds",
                        attempt + 1, MAX_ATTEMPTS, e, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                logger.error("Embedding call failed after %d attempts: %s", MAX_ATTEMPTS, e)
                raise EmbeddingServiceError(
                    f"{label} unreachable after {MAX_ATTEMPTS} attempts: {e}"
                ) from e

            if response.status_code in RETRYABLE_STATUS_CODES:
                last_error = httpx.HTTPStatusError(
                    f"status {response.status_code}",
                    request=response.request,
                    response=response,
                )
                if attempt < MAX_ATTEMPTS - 1:
                    delay = RETRY_DELAYS_SECONDS[attempt]
                    logger.warning(
                        "Embedding call %d/%d got HTTP %d — retrying in %ds",
                        attempt + 1, MAX_ATTEMPTS, response.status_code, delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                raise EmbeddingServiceError(
                    f"{label} returned {response.status_code} after {MAX_ATTEMPTS} attempts"
                ) from last_error

            if response.status_code >= 400:
                # The body preview carries the provider's own error detail
                # (DashScope's {"code","message"} JSON identifies the cause).
                body_preview = response.text[:300] if response.text else ""
                raise EmbeddingServiceError(
                    f"{label} rejected request (HTTP {response.status_code}): {body_preview}"
                )

            return response.json()

        raise EmbeddingServiceError(
            f"Embedding call failed after {MAX_ATTEMPTS} attempts: {last_error}"
        )
