"""Fusion algorithms for hybrid search."""
from typing import List, Dict, Any


def reciprocal_rank_fusion(
    vector_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
    k: int = 60,
    top_k: int = 50
) -> List[Dict[str, Any]]:
    """
    Reciprocal Rank Fusion (RRF) algorithm.

    RRF_score(d) = Σ 1/(k + rank_i(d))

    This fusion method combines rankings from multiple retrieval systems
    without requiring score normalization.

    Args:
        vector_results: Results from vector search
        bm25_results: Results from BM25 search
        k: Constant parameter (default 60, typical range 30-100)
        top_k: Number of results to return

    Returns:
        Fused and ranked results
    """
    scores: Dict[str, float] = {}
    doc_info: Dict[str, Dict[str, Any]] = {}

    # Process vector results
    for rank, doc in enumerate(vector_results):
        doc_id = doc.get("id") or doc.get("chunk_id")
        if not doc_id:
            continue

        rrf_score = 1.0 / (k + rank + 1)  # +1 to avoid division by zero
        scores[doc_id] = scores.get(doc_id, 0) + rrf_score

        # Store doc info (prefer vector result's metadata)
        if doc_id not in doc_info:
            doc_info[doc_id] = {
                "id": doc_id,
                "content": doc.get("content", ""),
                "hierarchy": doc.get("hierarchy", {}),
                "metadata": doc.get("metadata", {}),
                "rerank_score": doc.get("score", 0),
                "sources": ["vector"]
            }
        else:
            doc_info[doc_id]["sources"].append("vector")

    # Process BM25 results
    for rank, doc in enumerate(bm25_results):
        doc_id = doc.get("id") or doc.get("chunk_id")
        if not doc_id:
            continue

        rrf_score = 1.0 / (k + rank + 1)
        scores[doc_id] = scores.get(doc_id, 0) + rrf_score

        if doc_id not in doc_info:
            doc_info[doc_id] = {
                "id": doc_id,
                "content": doc.get("content", ""),
                "hierarchy": doc.get("hierarchy", {}),
                "metadata": doc.get("metadata", {}),
                "rerank_score": doc.get("score", 0),
                "sources": ["bm25"]
            }
        else:
            doc_info[doc_id]["sources"].append("bm25")
            # Add BM25 score to metadata
            doc_info[doc_id]["metadata"]["bm25_score"] = doc.get("score", 0)

    # Sort by RRF score
    sorted_docs = sorted(
        scores.items(),
        key=lambda x: -x[1]
    )[:top_k]

    # Build final results
    results = []
    for rank, (doc_id, rrf_score) in enumerate(sorted_docs):
        result = {
            **doc_info[doc_id],
            "chunk_id": doc_id,  # Add chunk_id for compatibility
            "rrf_score": rrf_score,
            "rank": rank + 1
        }
        results.append(result)

    return results


def score_normalized_fusion(
    vector_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
    alpha: float = 0.5,
    top_k: int = 50
) -> List[Dict[str, Any]]:
    """
    Score-level fusion with normalized scores.

    final_score = alpha * normalized_vector_score + (1 - alpha) * normalized_bm25_score

    Args:
        vector_results: Results from vector search
        bm25_results: Results from BM25 search
        alpha: Weight for vector scores (0-1), higher = more weight on vector
        top_k: Number of results to return

    Returns:
        Fused and ranked results
    """
    # Normalize vector scores
    vector_scores = [d.get("score", 0) for d in vector_results]
    max_vector = max(vector_scores) if vector_scores else 1

    # Normalize BM25 scores
    bm25_scores = [d.get("score", 0) for d in bm25_results]
    max_bm25 = max(bm25_scores) if bm25_scores else 1

    # Build score map
    doc_scores: Dict[str, Dict[str, Any]] = {}

    for doc in vector_results:
        doc_id = doc.get("id") or doc.get("chunk_id")
        if not doc_id:
            continue

        normalized = doc.get("score", 0) / max_vector if max_vector > 0 else 0
        doc_scores[doc_id] = {
            "id": doc_id,
            "content": doc.get("content", ""),
            "hierarchy": doc.get("hierarchy", {}),
            "metadata": doc.get("metadata", {}),
            "vector_score": normalized,
            "bm25_score": 0,
            "combined_score": alpha * normalized
        }

    for doc in bm25_results:
        doc_id = doc.get("id") or doc.get("chunk_id")
        if not doc_id:
            continue

        normalized = doc.get("score", 0) / max_bm25 if max_bm25 > 0 else 0

        if doc_id in doc_scores:
            doc_scores[doc_id]["bm25_score"] = normalized
            doc_scores[doc_id]["combined_score"] = (
                alpha * doc_scores[doc_id]["vector_score"] +
                (1 - alpha) * normalized
            )
        else:
            doc_scores[doc_id] = {
                "id": doc_id,
                "content": doc.get("content", ""),
                "hierarchy": doc.get("hierarchy", {}),
                "metadata": doc.get("metadata", {}),
                "vector_score": 0,
                "bm25_score": normalized,
                "combined_score": (1 - alpha) * normalized
            }

    # Sort by combined score
    sorted_docs = sorted(
        doc_scores.values(),
        key=lambda x: -x["combined_score"]
    )[:top_k]

    results = []
    for rank, doc in enumerate(sorted_docs):
        results.append({
            **doc,
            "chunk_id": doc.get("id"),  # Add chunk_id for compatibility
            "rank": rank + 1
        })

    return results


def deduplicate_results(
    results: List[Dict[str, Any]],
    key: str = "id"
) -> List[Dict[str, Any]]:
    """
    Remove duplicate results, keeping the first occurrence.

    Args:
        results: List of results
        key: Key to use for deduplication

    Returns:
        Deduplicated results
    """
    seen = set()
    unique = []

    for doc in results:
        doc_key = doc.get(key)
        if doc_key and doc_key not in seen:
            seen.add(doc_key)
            unique.append(doc)

    return unique