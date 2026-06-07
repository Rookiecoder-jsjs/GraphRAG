"""ChromaDB client for vector operations."""
import chromadb
from chromadb.config import Settings as ChromaSettings
from typing import List, Dict, Any, Optional

from app.config import get_settings


class ChromaClient:
    """Client for ChromaDB vector database operations."""

    def __init__(self):
        self.settings = get_settings()
        self._client: Optional[chromadb.HttpClient] = None
        self._collection: Optional[chromadb.Collection] = None

    def connect(self):
        """Initialize ChromaDB connection."""
        if self._client is None:
            self._client = chromadb.HttpClient(
                host=self.settings.CHROMA_HOST,
                port=self.settings.CHROMA_PORT
            )
            self._collection = self._client.get_or_create_collection(
                name="knowledge_graph_chunks",
                metadata={"hnsw:space": "cosine"}
            )

    def close(self):
        """Close ChromaDB connection."""
        self._collection = None
        self._client = None

    def add_chunks(
        self,
        chunk_ids: List[str],
        documents: List[str],
        embeddings: List[List[float]],
        metadatas: List[Dict[str, Any]]
    ):
        """Add chunks to vector database."""
        if self._collection is None:
            self.connect()

        cleaned_metadatas = []
        for metadata in metadatas:
            cleaned = {}
            for key, value in metadata.items():
                if value is None:
                    continue
                elif isinstance(value, list):
                    cleaned[key] = ", ".join(str(v) for v in value)
                else:
                    cleaned[key] = value
            cleaned_metadatas.append(cleaned)

        self._collection.add(
            ids=chunk_ids,
            documents=documents,
            embeddings=embeddings,
            metadatas=cleaned_metadatas
        )

    def search(
        self,
        query_embedding: List[float],
        user_id: int,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """Search for similar chunks."""
        if self._collection is None:
            self.connect()

        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k * 2,  # Get more results for filtering
            where={"user_id": str(user_id)}  # Chroma stores user_id as string
        )

        # Filter out results with None documents
        valid_indices = [i for i in range(len(results["ids"][0]))
                        if results["documents"][0][i] is not None]

        # Limit to top_k valid results
        valid_indices = valid_indices[:top_k]

        chunks = []
        for i in valid_indices:
            chunk = {
                "chunk_id": results["ids"][0][i],
                "content": results["documents"][0][i],
                "metadata": results["metadatas"][0][i],
                "distance": results["distances"][0][i] if results["distances"] else None
            }
            chunks.append(chunk)

        return chunks

    def get_chunk_context(
        self,
        chunk_id: str,
        user_id: int,
        window_size: int = 1
    ) -> List[Dict[str, Any]]:
        """Get context chunks around a given chunk."""
        if self._collection is None:
            self.connect()

        result = self._collection.get(
            ids=[chunk_id],
            where={"user_id": str(user_id)}
        )

        if not result["ids"]:
            return []

        metadata = result["metadatas"][0]
        related_ids = []

        for i in range(1, window_size + 1):
            prev_key = f"prev_chunk_id"
            next_key = f"next_chunk_id"
            if prev_key in metadata and metadata[prev_key]:
                related_ids.append(metadata[prev_key])
            if next_key in metadata and metadata[next_key]:
                related_ids.append(metadata[next_key])

        if not related_ids:
            return []

        related = self._collection.get(
            ids=related_ids,
            where={"user_id": str(user_id)}
        )

        context_chunks = []
        for i in range(len(related["ids"])):
            context_chunks.append({
                "chunk_id": related["ids"][i],
                "content": related["documents"][i],
                "metadata": related["metadatas"][i]
            })

        return context_chunks

    def get_chunks_by_ids(
        self,
        chunk_ids: List[str],
        user_id: int,
    ) -> List[Dict[str, Any]]:
        """Fetch a batch of chunks by their IDs, scoped to one user.

        Used by the graph-RAG path: the graph returns candidate chunk_ids
        that MENTION a query entity, and we need the actual content
        (which only lives in ChromaDB) to feed the reranker / LLM.

        Returns the same shape as `search()`: list of dicts with
        `chunk_id`, `content`, `metadata`. Order matches the input
        `chunk_ids`. Chunks that don't exist (or don't belong to the user)
        are silently skipped.
        """
        if not chunk_ids:
            return []
        if self._collection is None:
            self.connect()

        result = self._collection.get(
            ids=list(chunk_ids),
            where={"user_id": str(user_id)},
        )

        # Re-key by id so we can re-order / skip missing entries.
        by_id: Dict[str, Dict[str, Any]] = {}
        for i, cid in enumerate(result["ids"]):
            by_id[cid] = {
                "chunk_id": cid,
                "content": result["documents"][i],
                "metadata": result["metadatas"][i],
            }
        return [by_id[cid] for cid in chunk_ids if cid in by_id]

    def delete_document_chunks(self, document_id: str, user_id: int):
        """Delete all chunks for a document."""
        if self._collection is None:
            self.connect()

        self._collection.delete(
            where={"$and": [{"document_id": document_id}, {"user_id": str(user_id)}]}
        )

    def delete_user_chunks(self, user_id: int):
        """Delete all chunks for a user."""
        if self._collection is None:
            self.connect()

        self._collection.delete(
            where={"user_id": str(user_id)}
        )


_chroma_client: Optional[ChromaClient] = None


def get_chroma_client() -> ChromaClient:
    """Get singleton ChromaDB client instance."""
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = ChromaClient()
        _chroma_client.connect()
    return _chroma_client
