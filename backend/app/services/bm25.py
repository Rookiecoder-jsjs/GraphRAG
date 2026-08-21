"""BM25 retrieval service for hybrid search with multi-user support."""
import asyncio
import logging
import re
import threading
from typing import List, Dict, Any, Optional, Set

import jieba
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)


class BM25Service:
    """BM25 sparse retrieval service for hybrid search.

    This service maintains a per-user BM25 index for multi-tenant isolation.
    """

    def __init__(self):
        # User-specific indexes: user_id -> (BM25Okapi, doc_ids, doc_contents)
        self._user_indexes: Dict[int, Dict[str, Any]] = {}
        # Mutations (build/add/remove) read the old index, rebuild, then swap
        # the dict entry - concurrent calls would race and one side's update
        # would be silently lost. They run on worker threads (asyncio.to_thread
        # in the upload pipeline and the startup prewarm), so a plain
        # threading.Lock serialises them. `search` deliberately does NOT take
        # the lock: it grabs a snapshot reference and the swap is atomic.
        self._lock = threading.Lock()

    def build_user_index(
        self,
        user_id: int,
        documents: List[str],
        doc_ids: List[str]
    ):
        """
        Build BM25 index for a specific user.

        Args:
            user_id: User ID for isolation
            documents: List of document texts
            doc_ids: List of document IDs corresponding to texts
        """
        if not documents:
            return

        tokenized_docs = [self._tokenize(doc) for doc in documents]

        with self._lock:
            self._user_indexes[user_id] = {
                "index": BM25Okapi(tokenized_docs),
                "doc_ids": doc_ids,
                "doc_contents": {id_: doc for id_, doc in zip(doc_ids, documents)}
            }

    def add_to_index(
        self,
        user_id: int,
        documents: List[str],
        doc_ids: List[str]
    ):
        """Add documents to existing user index."""
        # Tokenise outside the lock (CPU-bound, no shared state) so the
        # critical section stays short. The existence check happens INSIDE
        # the lock: two cold-start threads racing here would otherwise both
        # build from scratch and the second build would drop the first's docs.
        tokenized_new = [self._tokenize(doc) for doc in documents]

        with self._lock:
            if user_id not in self._user_indexes:
                self._user_indexes[user_id] = {
                    "index": BM25Okapi(tokenized_new),
                    "doc_ids": list(doc_ids),
                    "doc_contents": {
                        id_: doc for id_, doc in zip(doc_ids, documents)
                    },
                }
                return

            user_index = self._user_indexes[user_id]

            # Add new documents to existing index
            all_doc_ids = user_index["doc_ids"] + doc_ids
            all_contents = {**user_index["doc_contents"]}
            all_contents.update({id_: doc for id_, doc in zip(doc_ids, documents)})

            # Rebuild BM25 index
            all_tokenized = [
                self._tokenize(all_contents[id_])
                for id_ in all_doc_ids
            ]

            self._user_indexes[user_id] = {
                "index": BM25Okapi(all_tokenized),
                "doc_ids": all_doc_ids,
                "doc_contents": all_contents
            }

    def remove_from_index(
        self,
        user_id: int,
        doc_ids: Set[str]
    ):
        """Remove documents from user index."""
        if user_id not in self._user_indexes:
            return

        with self._lock:
            user_index = self._user_indexes[user_id]

            # Filter out removed documents
            new_doc_ids = [id_ for id_ in user_index["doc_ids"] if id_ not in doc_ids]
            new_contents = {
                id_: content
                for id_, content in user_index["doc_contents"].items()
                if id_ not in doc_ids
            }

            if not new_doc_ids:
                # Remove entire user index
                del self._user_indexes[user_id]
                return

            # Rebuild index
            all_tokenized = [
                self._tokenize(new_contents[id_])
                for id_ in new_doc_ids
            ]

            self._user_indexes[user_id] = {
                "index": BM25Okapi(all_tokenized),
                "doc_ids": new_doc_ids,
                "doc_contents": new_contents
            }

    def search(
        self,
        query: str,
        user_id: int,
        top_k: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search BM25 index for a specific user.

        Args:
            query: Search query
            user_id: User ID for isolation
            top_k: Number of results to return

        Returns:
            List of search results with id, content, and score
        """
        if user_id not in self._user_indexes:
            return []

        user_index = self._user_indexes[user_id]
        bm25_index = user_index["index"]

        query_tokens = self._tokenize(query)
        scores = bm25_index.get_scores(query_tokens)

        # Get top-k indices
        indices = sorted(
            range(len(scores)),
            key=lambda i: scores[i],
            reverse=True
        )[:top_k]

        results = []
        for idx in indices:
            if scores[idx] > 0:
                doc_id = user_index["doc_ids"][idx]
                results.append({
                    "id": doc_id,
                    "content": user_index["doc_contents"].get(doc_id, ""),
                    "score": scores[idx],
                    "rank": len(results) + 1
                })

        return results

    def _tokenize(self, text: str) -> List[str]:
        """Tokenize for BM25.

        Uses jieba word segmentation so Chinese text is split into real
        words. The old regex grabbed the longest run of Unicode word chars,
        which lumped an entire contiguous Chinese sentence into ONE token \u2014
        a query then only matched on near-exact substrings, gutting Chinese
        recall in the keyword channel. Pure punctuation/separator segments
        are dropped; single-char tokens are noise.
        """
        tokens: List[str] = []
        for seg in jieba.lcut(text or ""):
            seg = seg.strip().lower()
            if not seg:
                continue
            # CJK ideographs count as \w in Unicode mode, so real Chinese
            # words fall through this check; only pure punctuation/space/
            # underscore segments are dropped.
            if re.fullmatch(r"[\W_]+", seg):
                continue
            if len(seg) < 2:
                continue
            tokens.append(seg)
        return tokens

    def clear_user(self, user_id: int):
        """Clear index for a specific user."""
        with self._lock:
            self._user_indexes.pop(user_id, None)

    def has_index(self, user_id: int) -> bool:
        """Check if user has BM25 index."""
        return user_id in self._user_indexes


# Prewarm status, surfaced via /health/ready. `done` means the background
# task has finished (success or failure); callers should not block on it.
_prewarm_state = {"done": False, "users": 0, "error": None}


async def prewarm_all_bm25() -> None:
    """Build BM25 indexes for every user that has chunks.

    Run as a background task at startup (non-blocking) so the first query
    from any user doesn't pay a full SQLite scan + re-tokenise on the
    request path. Idempotent: skips users whose index already exists (e.g.
    the lazy build in ``retrieve`` raced ahead). Failures are logged, not
    raised - prewarm is an optimisation, never a startup requirement.
    """
    global _prewarm_state
    from app.database import get_db
    bm25 = get_bm25_service()
    try:
        async with get_db() as db:
            async with db.execute("SELECT DISTINCT user_id FROM chunks") as cur:
                user_rows = await cur.fetchall()
        for row in user_rows:
            uid = row["user_id"]
            if bm25.has_index(uid):
                continue
            async with get_db() as db:
                async with db.execute(
                    "SELECT chunk_id, content FROM chunks WHERE user_id = ? ORDER BY created_at",
                    (uid,),
                ) as cur:
                    rows = await cur.fetchall()
            if rows:
                # Index building (tokenise + BM25 stats) is CPU-bound - run
                # it off the event loop so prewarm never blocks startup I/O.
                await asyncio.to_thread(
                    bm25.build_user_index,
                    uid,
                    [r["content"] for r in rows],
                    [r["chunk_id"] for r in rows],
                )
                logger.info("BM25 prewarmed for user_id=%d (%d chunks)", uid, len(rows))
        _prewarm_state = {"done": True, "users": len(user_rows), "error": None}
    except Exception as e:
        logger.warning("BM25 prewarm failed: %s", e)
        _prewarm_state = {"done": True, "users": 0, "error": str(e)}


def get_prewarm_state() -> dict:
    """Return a snapshot of the BM25 prewarm status for health checks."""
    return dict(_prewarm_state)


# Singleton instance
_bm25_service: Optional[BM25Service] = None


def get_bm25_service() -> BM25Service:
    """Get singleton BM25 service instance."""
    global _bm25_service
    if _bm25_service is None:
        _bm25_service = BM25Service()
    return _bm25_service