"""Embedding service (SiliconFlow / DashScope providers).

Two providers behind one interface, selected by ``settings.EMBEDDING_PROVIDER``:

- ``siliconflow``: OpenAI-compatible ``POST /embeddings`` (text only).
- ``dashscope``: DashScope NATIVE multimodal endpoint (text + images; the
  compat endpoint 404s for ``qwen3-vl-embedding`` — verified by
  scripts/probe_vl_embedding.py). Auth uses ``BAILIAN_API_KEY``; every call
  pins ``parameters.dimension`` to ``EMBEDDING_DIM`` via MRL.

The provider is a HARD switch, not a failover: vector spaces are
incompatible, so switching requires a full re-embed (migrate_embeddings.py).

Reliability (shared by both providers): caching, exponential backoff, and
self-healing cache.
"""
import asyncio
import base64
import hashlib
import json
import logging
from datetime import datetime
from typing import List, Optional

import aiosqlite
import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


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
REQUEST_TIMEOUT_SECONDS = 60.0
EMBED_BATCH_SIZE = 5


class EmbeddingServiceError(Exception):
    """Raised when the embedding service cannot produce a vector after all retries."""


def _serialize_embedding(embedding: List[float]) -> bytes:
    """JSON-encode a float vector to bytes for SQLite storage."""
    return json.dumps(embedding, separators=(",", ":")).encode("utf-8")


def _deserialize_embedding(blob: bytes) -> List[float]:
    """Decode a JSON-encoded float vector. Raises on corrupted cache rows."""
    return json.loads(blob.decode("utf-8"))


def _looks_like_json_embedding(blob: bytes) -> bool:
    """Cheap sniff: a valid JSON array of numbers starts with '[', '-', or a digit.

    Catches the legacy case where a previous version of this service stored
    pickled blobs (which start with a non-UTF-8 byte such as 0x80) and would
    blow up json.loads with 'utf-8 codec can't decode byte 0x80'.
    """
    if not blob:
        return False
    first = blob[:1]
    if first == b"[":
        return True
    if first == b"-":
        return True
    return b"0" <= first <= b"9"


class EmbeddingService:
    """Service for generating embeddings via SiliconFlow or DashScope.

    Reliability features:
      - Exponential backoff on transport / 5xx errors (5 attempts: 1,2,4,8,16s).
      - Shared keep-alive httpx.AsyncClient (one TLS handshake, reused across
        calls; a stale connection surfaces as RemoteProtocolError, which the
        retry policy handles).
      - 4xx errors are NOT retried — they are surfaced immediately.
      - Self-healing cache: rows whose bytes do not look like JSON are deleted
        on read, so legacy pickle data cannot poison the cache forever.
      - On final failure, raises EmbeddingServiceError instead of silently
        returning a zero vector (which would later corrupt retrieval).
    """

    def __init__(self):
        self.settings = get_settings()
        self.provider = self.settings.EMBEDDING_PROVIDER.lower()
        if self.provider == "dashscope":
            self._embed_url = self.settings.DASHSCOPE_EMBEDDING_URL
            # Prefer the dedicated VL-embedding key (scoped quota); fall
            # back to the LLM's Bailian key when the dedicated one is
            # absent so single-key deployments keep working.
            self.api_key = (
                self.settings.BAILIAN_API_KEY_QWEN_VL_EMBEDDING
                or self.settings.BAILIAN_API_KEY
            )
            self.model = self.settings.DASHSCOPE_EMBEDDING_MODEL
        else:  # "siliconflow" — validated against _EMBEDDING_PROVIDERS in config
            self._embed_url = f"{self.settings.SILICON_FLOW_BASE_URL}/embeddings"
            self.api_key = self.settings.SILICON_FLOW_API_KEY
            self.model = self.settings.EMBEDDING_MODEL
        self._semaphore = asyncio.Semaphore(5)
        self._client: Optional[httpx.AsyncClient] = None
        logger.info(
            "EmbeddingService ready: provider=%s model=%s key=%s",
            self.provider, self.model,
            "dedicated" if self.settings.BAILIAN_API_KEY_QWEN_VL_EMBEDDING else "shared",
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared HTTP client.

        Reusing one client keeps the TLS connection to SiliconFlow warm
        across calls — the old per-attempt client paid a fresh TCP+TLS
        handshake (~100–300ms) on EVERY embedding request, which dominated
        latency for single-text query embeddings and serialized batch
        uploads alike.
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT_SECONDS,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def close(self) -> None:
        """Close the shared HTTP client (no-op if never created)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _delete_corrupt_cache_row(self, db, text_hash: str, reason: str) -> None:
        """Remove a single corrupt cache row."""
        try:
            await db.execute(
                "DELETE FROM embedding_cache WHERE text_hash = ? AND model = ?",
                (text_hash, self.model),
            )
            await db.commit()
        except Exception as cleanup_error:
            logger.warning("Failed to delete corrupt cache row %s: %s", text_hash, cleanup_error)
        logger.warning("Corrupt embedding cache row %s deleted (%s)", text_hash, reason)

    async def _get_cached_embedding(self, text_hash: str) -> Optional[List[float]]:
        """Try to get embedding from cache. Self-heals on corrupted rows."""
        async with aiosqlite.connect(self.settings.SQLITE_PATH) as db:
            async with db.execute(
                "SELECT embedding FROM embedding_cache WHERE text_hash = ? AND model = ?",
                (text_hash, self.model),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                blob = row[0]
                if not _looks_like_json_embedding(blob):
                    await self._delete_corrupt_cache_row(
                        db, text_hash, f"non-JSON prefix byte=0x{blob[:1].hex()}"
                    )
                    return None
                try:
                    return _deserialize_embedding(blob)
                except (ValueError, UnicodeDecodeError) as e:
                    await self._delete_corrupt_cache_row(db, text_hash, str(e))
                    return None
        return None

    async def _cache_embedding(self, text_hash: str, text: str, embedding: List[float]) -> None:
        """Cache embedding to database. Best-effort; failures are logged, not raised."""
        try:
            async with aiosqlite.connect(self.settings.SQLITE_PATH) as db:
                await db.execute(
                    """INSERT OR REPLACE INTO embedding_cache
                       (text_hash, text, embedding, model, created_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (text_hash, text, _serialize_embedding(embedding), self.model, datetime.now()),
                )
                await db.commit()
        except Exception as e:
            logger.warning("Failed to cache embedding: %s", e, exc_info=True)

    def _get_text_hash(self, text: str) -> str:
        return self._content_hash(text.encode("utf-8"))

    @staticmethod
    def _content_hash(data: bytes) -> str:
        """md5 over raw bytes — the cache key for any modality.

        For text this is byte-identical to the old md5(utf-8) keys, so
        existing cache rows keep hitting; images hash their binary content.
        """
        return hashlib.md5(data).hexdigest()

    def _build_text_payload(self, texts: List[str]) -> dict:
        """Build the request body for a homogeneous TEXT batch.

        siliconflow: OpenAI-compatible shape — single text goes out as a bare
        string, a batch as a list (byte-identical to the pre-provider-split
        wire format, so rollback behavior is unchanged). Deliberately does NOT
        pin `dimensions` — the SiliconFlow model's native dim is what the
        existing collection was built with.

        dashscope: native multimodal shape, one ``{"text": t}`` content item
        per input; ``parameters.dimension`` pins the MRL output to
        EMBEDDING_DIM (probe T11, 2026-08-02).
        """
        if self.provider == "dashscope":
            return {
                "model": self.model,
                "input": {"contents": [{"text": text} for text in texts]},
                "parameters": {"dimension": self.settings.EMBEDDING_DIM},
            }
        return {
            "model": self.model,
            "input": texts[0] if len(texts) == 1 else texts,
            "encoding_format": "float",
        }

    def _parse_embeddings(self, data: dict, expected: int) -> List[List[float]]:
        """Extract vectors from a provider response, in input order.

        Raises:
            EmbeddingServiceError: on malformed payloads, a count mismatch,
                or — for dashscope — dimension drift away from EMBEDDING_DIM
                (an MRL guard: a silently-wrong dim would corrupt the Chroma
                collection on upsert).
        """
        try:
            if self.provider == "dashscope":
                items = sorted(
                    (data.get("output") or {}).get("embeddings") or [],
                    key=lambda item: item.get("index", 0),
                )
                embeddings = [item["embedding"] for item in items]
            else:
                embeddings = [item["embedding"] for item in data["data"]]
        except (KeyError, TypeError) as e:
            raise EmbeddingServiceError(
                f"Malformed {self.provider} response: {e}"
            ) from e

        if len(embeddings) != expected:
            raise EmbeddingServiceError(
                f"{self.provider} returned {len(embeddings)} embeddings "
                f"for {expected} inputs"
            )
        if self.provider == "dashscope":
            for embedding in embeddings:
                if len(embedding) != self.settings.EMBEDDING_DIM:
                    raise EmbeddingServiceError(
                        f"dashscope returned dim {len(embedding)}, expected "
                        f"{self.settings.EMBEDDING_DIM} — MRL drift, check "
                        "parameters.dimension"
                    )
        return embeddings

    async def _call_with_retry(self, payload: dict) -> dict:
        """POST to the active provider with exponential backoff.

        Raises:
            EmbeddingServiceError: after MAX_ATTEMPTS exhausted on retryable error
                or immediately on a 4xx response.
        """
        last_error: Optional[BaseException] = None
        label = "DashScope" if self.provider == "dashscope" else "SiliconFlow"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        client = await self._get_client()

        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await client.post(self._embed_url, headers=headers, json=payload)
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

    async def embed_single(self, text: str, use_cache: bool = True) -> List[float]:
        """Embed a single text with caching. Raises EmbeddingServiceError on API failure."""
        if not text.strip():
            return [0.0] * self.settings.EMBEDDING_DIM

        text_hash = self._get_text_hash(text)

        if use_cache:
            cached = await self._get_cached_embedding(text_hash)
            if cached is not None:
                return cached

        async with self._semaphore:
            data = await self._call_with_retry(self._build_text_payload([text]))

        embedding = self._parse_embeddings(data, expected=1)[0]

        if use_cache:
            await self._cache_embedding(text_hash, text, embedding)
        return embedding

    async def embed_batch(
        self, texts: List[str], use_cache: bool = True
    ) -> List[List[float]]:
        """Embed multiple texts with caching and batching.

        For each batch: consult cache first, send the remainder to the API
        (rate-limited via semaphore), cache results. Raises EmbeddingServiceError
        on unrecoverable API failure.
        """
        if not texts:
            return []

        results: List[Optional[List[float]]] = [None] * len(texts)
        texts_to_embed: List[str] = []
        indices_to_embed: List[int] = []
        hashes_to_embed: List[str] = []

        for i, text in enumerate(texts):
            if not text.strip():
                results[i] = [0.0] * self.settings.EMBEDDING_DIM
                continue
            text_hash = self._get_text_hash(text)
            if use_cache:
                cached = await self._get_cached_embedding(text_hash)
                if cached is not None:
                    results[i] = cached
                    continue
            texts_to_embed.append(text)
            indices_to_embed.append(i)
            hashes_to_embed.append(text_hash)

        for batch_start in range(0, len(texts_to_embed), EMBED_BATCH_SIZE):
            batch_texts = texts_to_embed[batch_start : batch_start + EMBED_BATCH_SIZE]
            batch_indices = indices_to_embed[batch_start : batch_start + EMBED_BATCH_SIZE]
            batch_hashes = hashes_to_embed[batch_start : batch_start + EMBED_BATCH_SIZE]

            async with self._semaphore:
                data = await self._call_with_retry(
                    self._build_text_payload(batch_texts)
                )

            embeddings = self._parse_embeddings(data, expected=len(batch_texts))

            for original_idx, embedding, text, text_hash in zip(
                batch_indices, embeddings, batch_texts, batch_hashes
            ):
                results[original_idx] = embedding
                if use_cache:
                    await self._cache_embedding(text_hash, text, embedding)

            if batch_start + EMBED_BATCH_SIZE < len(texts_to_embed):
                await asyncio.sleep(0.3)

        if any(r is None for r in results):
            missing = [i for i, r in enumerate(results) if r is None]
            raise EmbeddingServiceError(
                f"embed_batch left {len(missing)} slot(s) unfilled (indices={missing})"
            )

        return [r for r in results]  # type: ignore[misc]

    async def embed_image_bytes(
        self, image_bytes: bytes, media_type: str, use_cache: bool = True
    ) -> List[float]:
        """Embed a single image (PNG/JPEG bytes) via the DashScope provider.

        Images exist only in the multimodal vector space, so this loudly
        refuses under any other provider instead of returning a zero vector
        that would poison cross-modal retrieval.

        Raises:
            ValueError: empty bytes (caller bug — nothing to embed).
            EmbeddingServiceError: wrong provider, API failure after all
                retries, or a malformed / dimension-drifted response.
        """
        if not image_bytes:
            raise ValueError("embed_image_bytes requires non-empty image bytes")
        if self.provider != "dashscope":
            raise EmbeddingServiceError(
                "image embedding requires EMBEDDING_PROVIDER=dashscope "
                f"(active provider: {self.provider})"
            )

        content_hash = self._content_hash(image_bytes)
        # The cache row's `text` column is NOT NULL — store a human-readable
        # descriptor instead of the binary (key = content hash, value = the
        # vector BLOB, same as text rows).
        descriptor = (
            f"image:{media_type}:sha256:"
            f"{hashlib.sha256(image_bytes).hexdigest()}:{len(image_bytes)}B"
        )

        if use_cache:
            cached = await self._get_cached_embedding(content_hash)
            if cached is not None:
                return cached

        data_uri = (
            f"data:{media_type};base64,"
            f"{base64.b64encode(image_bytes).decode('ascii')}"
        )
        payload = {
            "model": self.model,
            "input": {"contents": [{"image": data_uri}]},
            "parameters": {"dimension": self.settings.EMBEDDING_DIM},
        }

        async with self._semaphore:
            data = await self._call_with_retry(payload)

        embedding = self._parse_embeddings(data, expected=1)[0]

        if use_cache:
            await self._cache_embedding(content_hash, descriptor, embedding)
        return embedding


# Singleton instance
_embedding_service: Optional[EmbeddingService] = None


async def get_embedding_service() -> EmbeddingService:
    """Get singleton embedding service instance."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service


async def close_embedding_service() -> None:
    """Close the shared embedding HTTP client at shutdown (no-op if never created)."""
    global _embedding_service
    if _embedding_service is not None:
        await _embedding_service.close()
        _embedding_service = None
