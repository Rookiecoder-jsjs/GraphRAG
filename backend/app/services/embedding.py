"""Silicon Flow embedding service with caching, exponential backoff, and self-healing cache."""
import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import aiosqlite
import httpx

from app.config import get_settings
# Retryable classification is shared across llm/embedding/reranker (one
# definition of "worth retrying"); the schedule below is embedding-specific.
from app.services.retry import (
    RETRYABLE_EXCEPTIONS,
    RETRYABLE_STATUS_CODES,
)

logger = logging.getLogger(__name__)


MAX_ATTEMPTS = 5
RETRY_DELAYS_SECONDS = [1, 2, 4, 8, 16]
REQUEST_TIMEOUT_SECONDS = 60.0
# Inputs per /embeddings request moved to settings.EMBED_BATCH_SIZE (config.py).


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
        self.base_url = self.settings.SILICON_FLOW_BASE_URL
        self.api_key = self.settings.SILICON_FLOW_API_KEY
        self.model = self.settings.EMBEDDING_MODEL
        self._semaphore = asyncio.Semaphore(5)
        self._client: Optional[httpx.AsyncClient] = None
        # Shared cache connection: one aiosqlite connection (one worker
        # thread) reused for every cache read/write instead of a fresh
        # connect per call — a 300-chunk ingest used to open and close ~600
        # connections. aiosqlite serializes operations on the connection,
        # which is fine for cache-sized queries.
        self._db: Optional[aiosqlite.Connection] = None
        self._db_lock = asyncio.Lock()

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
        """Close the shared HTTP client and cache connection (no-op if never created)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def _get_db(self) -> aiosqlite.Connection:
        """Lazily open the shared cache connection (same PRAGMAs as get_db).

        The connection lives for the service lifetime; on Windows that means
        the backend holds a handle on the SQLite file while running, which is
        the deliberate trade for not reconnecting on every cache access.
        """
        if self._db is None:
            async with self._db_lock:
                if self._db is None:
                    db = await aiosqlite.connect(self.settings.SQLITE_PATH)
                    await db.execute("PRAGMA busy_timeout = 5000")
                    await db.execute("PRAGMA journal_mode = WAL")
                    self._db = db
        return self._db

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

    async def _cached_lookup(self, text_hashes: List[str]) -> Dict[str, List[float]]:
        """Batch cache read: hash -> validated embedding.

        One SELECT for the whole batch (callers pass at most one batch's
        worth of hashes, well under the SQLite variable limit). Rows failing
        validation (non-JSON blob, wrong dimension) are self-healed exactly
        as before: the corrupt row is deleted and simply absent from the
        result (treated as a cache miss). A missing table is tolerated as an
        all-miss — the cache is best-effort by contract and init_db() may not
        have run (tests, CLI tools).
        """
        if not text_hashes:
            return {}
        placeholders = ",".join("?" * len(text_hashes))
        try:
            db = await self._get_db()
            async with db.execute(
                f"SELECT text_hash, embedding FROM embedding_cache "
                f"WHERE text_hash IN ({placeholders}) AND model = ?",
                (*text_hashes, self.model),
            ) as cursor:
                rows = await cursor.fetchall()
        except aiosqlite.OperationalError as e:
            logger.warning("Embedding cache read skipped (no table?): %s", e)
            return {}
        found: Dict[str, List[float]] = {}
        for text_hash, blob in rows:
            if not _looks_like_json_embedding(blob):
                await self._delete_corrupt_cache_row(
                    db, text_hash, f"non-JSON prefix byte=0x{blob[:1].hex()}"
                )
                continue
            try:
                embedding = _deserialize_embedding(blob)
            except (ValueError, UnicodeDecodeError) as e:
                await self._delete_corrupt_cache_row(db, text_hash, str(e))
                continue
            # Dimension guard: vectors cached by an older model config
            # (or before the `dimensions` param was added — Qwen3
            # defaults to 4096) would be rejected by Chroma with
            # InvalidDimensionException. Treat length mismatch as stale
            # and re-embed rather than surfacing a 500 later.
            if len(embedding) != self.settings.EMBEDDING_DIM:
                await self._delete_corrupt_cache_row(
                    db, text_hash,
                    f"dim {len(embedding)} != {self.settings.EMBEDDING_DIM}",
                )
                continue
            found[text_hash] = embedding
        return found

    async def _cache_rows(self, rows: List[Tuple[str, str, List[float]]]) -> None:
        """Batch cache write: one executemany + one commit.

        Best-effort; failures are logged, not raised (unchanged contract).
        """
        if not rows:
            return
        try:
            db = await self._get_db()
            await db.executemany(
                """INSERT OR REPLACE INTO embedding_cache
                   (text_hash, text, embedding, model, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [
                    (text_hash, text, _serialize_embedding(embedding),
                     self.model, datetime.now())
                    for text_hash, text, embedding in rows
                ],
            )
            await db.commit()
        except Exception as e:
            logger.warning("Failed to cache embeddings: %s", e, exc_info=True)

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
            cached_map = await self._cached_lookup([text_hash])
            if text_hash in cached_map:
                return cached_map[text_hash]

        async with self._semaphore:
            data = await self._call_with_retry(
                {
                    "model": self.model,
                    "input": text,
                    "encoding_format": "float",
                    # Qwen3-Embedding-8B defaults to 4096 dims; without an
                    # explicit `dimensions` the vector won't match the
                    # collection dimensionality (EMBEDDING_DIM) and Chroma
                    # rejects the query with InvalidDimensionException.
                    "dimensions": self.settings.EMBEDDING_DIM,
                }
            )

        try:
            embedding = data["data"][0]["embedding"]
        except (KeyError, IndexError, TypeError) as e:
            raise EmbeddingServiceError(f"Malformed SiliconFlow response: {e}") from e

        if use_cache:
            await self._cache_rows([(text_hash, text, embedding)])
        return embedding

    async def embed_batch(
        self, texts: List[str], use_cache: bool = True
    ) -> List[List[float]]:
        """Embed multiple texts with caching and batching.

        Cache misses are grouped into requests of ``settings.EMBED_BATCH_SIZE``
        inputs: the query path sends <=4 texts and always fits in one request,
        the ingest path scales its request count down by the batch size. For
        each batch: one semaphore-limited API call, one bulk cache write.
        Raises EmbeddingServiceError on unrecoverable API failure.
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
            texts_to_embed.append(text)
            indices_to_embed.append(i)
            hashes_to_embed.append(self._get_text_hash(text))

        if use_cache and texts_to_embed:
            # One round-trip for the whole batch instead of a connection per
            # text. Duplicate texts share a hash; every index with that hash
            # gets the same hit.
            cached_map = await self._cached_lookup(hashes_to_embed)
            for text, idx, text_hash in zip(
                texts_to_embed, indices_to_embed, hashes_to_embed
            ):
                if text_hash in cached_map:
                    results[idx] = cached_map[text_hash]
            remaining = [
                (text, idx, text_hash)
                for text, idx, text_hash in zip(
                    texts_to_embed, indices_to_embed, hashes_to_embed
                )
                if text_hash not in cached_map
            ]
            texts_to_embed = [t for t, _, _ in remaining]
            indices_to_embed = [i for _, i, _ in remaining]
            hashes_to_embed = [h for _, _, h in remaining]

        batch_size = max(1, self.settings.EMBED_BATCH_SIZE)
        for batch_start in range(0, len(texts_to_embed), batch_size):
            batch_texts = texts_to_embed[batch_start : batch_start + batch_size]
            batch_indices = indices_to_embed[batch_start : batch_start + batch_size]
            batch_hashes = hashes_to_embed[batch_start : batch_start + batch_size]

            async with self._semaphore:
                data = await self._call_with_retry(
                    {
                        "model": self.model,
                        "input": batch_texts,
                        "encoding_format": "float",
                        # Keep batch and single-embed dimensions in sync with
                        # the collection (EMBEDDING_DIM); see embed_single.
                        "dimensions": self.settings.EMBEDDING_DIM,
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

            new_rows: List[Tuple[str, str, List[float]]] = []
            for original_idx, embedding, text, text_hash in zip(
                batch_indices, embeddings, batch_texts, batch_hashes
            ):
                results[original_idx] = embedding
                if use_cache:
                    new_rows.append((text_hash, text, embedding))
            if new_rows:
                await self._cache_rows(new_rows)

            if batch_start + batch_size < len(texts_to_embed):
                await asyncio.sleep(self.settings.EMBED_BATCH_DELAY_SECONDS)

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


async def close_embedding_service() -> None:
    """Close the shared embedding HTTP client at shutdown (no-op if never created)."""
    global _embedding_service
    if _embedding_service is not None:
        await _embedding_service.close()
        _embedding_service = None
