"""BM25 retrieval service for hybrid search with multi-user support."""
import re
from typing import List, Dict, Any, Optional, Set
from rank_bm25 import BM25Okapi


class BM25Service:
    """BM25 sparse retrieval service for hybrid search.

    This service maintains a per-user BM25 index for multi-tenant isolation.
    """

    def __init__(self):
        # User-specific indexes: user_id -> (BM25Okapi, doc_ids, doc_contents)
        self._user_indexes: Dict[int, Dict[str, Any]] = {}

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
        if user_id not in self._user_indexes:
            self.build_user_index(user_id, documents, doc_ids)
            return

        user_index = self._user_indexes[user_id]

        # Add new documents to existing index
        tokenized_docs = [self._tokenize(doc) for doc in documents]

        # Rebuild index with new documents
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
        """Simple tokenization: lowercase, extract alphanumeric tokens."""
        # Extract alphanumeric tokens (supports Chinese, English)
        tokens = re.findall(r'[\w\u4e00-\u9fff]+', text.lower())
        # Filter out very short tokens
        tokens = [t for t in tokens if len(t) >= 2]
        return tokens

    def clear_user(self, user_id: int):
        """Clear index for a specific user."""
        if user_id in self._user_indexes:
            del self._user_indexes[user_id]

    def clear_all(self):
        """Clear all user indexes."""
        self._user_indexes = {}

    def has_index(self, user_id: int) -> bool:
        """Check if user has BM25 index."""
        return user_id in self._user_indexes


# Singleton instance
_bm25_service: Optional[BM25Service] = None


def get_bm25_service() -> BM25Service:
    """Get singleton BM25 service instance."""
    global _bm25_service
    if _bm25_service is None:
        _bm25_service = BM25Service()
    return _bm25_service