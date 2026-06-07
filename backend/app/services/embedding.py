"""Silicon Flow embedding service with caching, exponential backoff, and self-healing cache."""
import asyncio
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
    """Service for generating text embeddings using Silicon Flow API.

    Reliability features:
      - Exponential backoff on transport / 5xx errors (5 attempts: 1,2,4,8,16s).
      - Fresh httpx.AsyncClient per attempt (avoids keep-alive stale-connection reuse).
      - 4xx errors are NOT retried — they are surfaced immediately.
      - Self-healing cache: rows whose bytes do not look like JSON are deleted
        on read, so legacy pickle data cannot poison the cache forever.
      - On final failure, raises EmbeddingServiceError instead of silently
        returning a zero vector (which would later corrupt retrieval).
    """

    def __init__(self):
        self.settings = get_settings()
        self.base_url = self.settings.SILICON_FLOW_BASE_URL
        self.api_key = self.settings.SILICON_FLOW_API_KEY
        self.model = self.settings.EMBEDDING_MODEL
        self._semaphore = asyncio.Semaphore(5)

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
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    async def _call_with_retry(self, payload: dict) -> dict:
        """POST to SiliconFlow with exponential backoff.

        Raises:
            EmbeddingServiceError: after MAX_ATTEMPTS exhausted on retryable error
                or immediately on a 4xx response.
        """
        last_error: Optional[BaseException] = None
        url = f"{self.base_url}/embeddings"
        headers = {"Authorization": f"Bearer {self.api_key}"}

        for attempt in range(MAX_ATTEMPTS):
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
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
                        f"SiliconFlow unreachable after {MAX_ATTEMPTS} attempts: {e}"
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
                        f"SiliconFlow returned {response.status_code} after {MAX_ATTEMPTS} attempts"
                    ) from last_error

                if response.status_code >= 400:
                    body_preview = response.text[:300] if response.text else ""
                    raise EmbeddingServiceError(
                        f"SiliconFlow rejected request (HTTP {response.status_code}): {body_preview}"
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
            data = await self._call_with_retry(
                {"model": self.model, "input": text, "encoding_format": "float"}
            )

        try:
            embedding = data["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as e:
            raise EmbeddingServiceError(f"Malformed SiliconFlow response: {e}") from e

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
                    {
                        "model": self.model,
                        "input": batch_texts,
                        "encoding_format": "float",
                    }
                )

            try:
                embeddings = [item["embedding"] for item in data["data"]]
            except (KeyError, TypeError) as e:
                raise EmbeddingServiceError(f"Malformed SiliconFlow response: {e}") from e

            if len(embeddings) != len(batch_texts):
                raise EmbeddingServiceError(
                    f"SiliconFlow returned {len(embeddings)} embeddings for "
                    f"{len(batch_texts)} inputs"
                )

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


# Singleton instance
_embedding_service: Optional[EmbeddingService] = None


async def get_embedding_service() -> EmbeddingService:
    """Get singleton embedding service instance."""
    global _embedding_service
    if _embedding_service is None:
        _embedding_service = EmbeddingService()
    return _embedding_service
