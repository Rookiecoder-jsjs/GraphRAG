"""Embedding service — thin shell delegating to the active provider.

The provider registry (``app/services/providers/``) owns the vendor-
specific wire work (URL, auth, payload shape, response parsing, retry
policy); this shell owns the cross-provider concerns: the SQLite cache,
the concurrency semaphore, zero-vector handling for blank text, and the
content-hash cache keys.

``EMBEDDING_PROVIDER`` is a HARD switch, not a failover: the two
providers produce vectors in incompatible semantic spaces, so switching
requires a full re-embed (scripts/migrate_embeddings.py with the backend
stopped).
"""
import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import List, Optional

import aiosqlite

from app.config import get_settings
from app.services.providers import (
    EmbeddingServiceError,
    KIND_EMBEDDING,
    MAX_ATTEMPTS,  # noqa: F401  (re-exported for test/compat)
    RETRY_DELAYS_SECONDS,  # noqa: F401  (re-exported for test/compat)
    get_provider_class,
)

logger = logging.getLogger(__name__)

EMBED_BATCH_SIZE = 5


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
    """Service for generating embeddings — delegates to the active provider.

    Reliability features (shell-level, provider-agnostic):
      - SQLite cache keyed by content md5 + model (naturally namespaced
        per provider/model — switching providers never reuses vectors).
      - Self-healing cache: rows whose bytes do not look like JSON are
        deleted on read, so legacy pickle data cannot poison the cache.
      - Concurrency semaphore bounds in-flight provider calls.
      - On final failure raises EmbeddingServiceError instead of silently
        returning a zero vector (which would later corrupt retrieval).
    """

    def __init__(self):
        self.settings = get_settings()
        self.provider = self.settings.EMBEDDING_PROVIDER.lower()
        self._provider = get_provider_class(KIND_EMBEDDING, self.provider)(
            self.settings
        )
        self._semaphore = asyncio.Semaphore(5)
        logger.info(
            "EmbeddingService ready: provider=%s model=%s key=%s",
            self.provider, self._provider.model,
            "dedicated" if self.settings.BAILIAN_API_KEY_QWEN_VL_EMBEDDING else "shared",
        )

    async def close(self) -> None:
        """Close the provider's shared HTTP client (no-op if never created)."""
        await self._provider.close()

    async def _delete_corrupt_cache_row(self, db, text_hash: str, reason: str) -> None:
        """Remove a single corrupt cache row."""
        try:
            await db.execute(
                "DELETE FROM embedding_cache WHERE text_hash = ? AND model = ?",
                (text_hash, self._provider.model),
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
                (text_hash, self._provider.model),
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
                    (text_hash, text, _serialize_embedding(embedding), self._provider.model, datetime.now()),
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
            embedding = (await self._provider.embed_texts([text]))[0]

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
                embeddings = await self._provider.embed_texts(batch_texts)

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
        """Embed a single image (PNG/JPEG bytes) via the active provider.

        Images exist only in the multimodal vector space, so a text-only
        provider raises loudly instead of returning a zero vector that
        would poison cross-modal retrieval.

        Raises:
            ValueError: empty bytes (caller bug — nothing to embed).
            EmbeddingServiceError: provider has no image support, API
                failure after all retries, or a malformed / dimension-
                drifted response.
        """
        if not image_bytes:
            raise ValueError("embed_image_bytes requires non-empty image bytes")
        if not self._provider.supports_images:
            # Fail BEFORE the cache lookup: with a text-only provider the
            # request is rejected regardless of what the cache holds, and
            # checking first keeps the error deterministic even against a
            # cache DB that has no rows for this content.
            raise EmbeddingServiceError(
                "image embedding requires EMBEDDING_PROVIDER=dashscope "
                f"(active provider: {self._provider.provider_name})"
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

        async with self._semaphore:
            embedding = await self._provider.embed_image(image_bytes, media_type)

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
