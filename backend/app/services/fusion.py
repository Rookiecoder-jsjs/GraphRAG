"""Fusion algorithms for hybrid search."""
from typing import List, Dict, Any, Optional, Sequence


def reciprocal_rank_fusion_multi(
    result_lists: Sequence[Sequence[Dict[str, Any]]],
    k: int = 60,
    top_k: int = 50,
    weights: Optional[Sequence[float]] = None,
    labels: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """Reciprocal Rank Fusion over N ranked result lists.

    RRF_score(d) = Σ_i  weight_i * 1 / (k + rank_i(d) + 1)

    Each list is a ranked sequence of dicts carrying ``id`` or ``chunk_id``.
    ``weights`` (default all 1.0) lets a channel (e.g. graph) be up/down
    weighted. ``labels`` names each list for the per-doc ``sources`` field
    (defaults to ``list_0``, ``list_1``, …). RRF only uses rank positions,
    so lists with incomparable score scales (vector cosine vs BM25 vs graph
    match) fuse cleanly without normalisation.
    """
    n = len(result_lists)
    if n == 0:
        return []
    if weights is None:
        weights = [1.0] * n
    if labels is None:
        labels = [f"list_{i}" for i in range(n)]

    scores: Dict[str, float] = {}
    doc_info: Dict[str, Dict[str, Any]] = {}

    for li, results in enumerate(result_lists):
        w = weights[li] if li < len(weights) else 1.0
        label = labels[li] if li < len(labels) else f"list_{li}"
        for rank, doc in enumerate(results):
            doc_id = doc.get("id") or doc.get("chunk_id")
            if not doc_id:
                continue
            rrf_score = w * (1.0 / (k + rank + 1))
            scores[doc_id] = scores.get(doc_id, 0.0) + rrf_score
            if doc_id not in doc_info:
                doc_info[doc_id] = {
                    "id": doc_id,
                    "content": doc.get("content", ""),
                    "hierarchy": doc.get("hierarchy", {}),
                    "metadata": dict(doc.get("metadata") or {}),
                    "sources": [label],
                }
            else:
                if label not in doc_info[doc_id]["sources"]:
                    doc_info[doc_id]["sources"].append(label)

    sorted_docs = sorted(scores.items(), key=lambda x: -x[1])[:top_k]

    results: List[Dict[str, Any]] = []
    for rank, (doc_id, rrf_score) in enumerate(sorted_docs):
        info = doc_info[doc_id]
        results.append({
            **info,
            "chunk_id": doc_id,
            "rrf_score": rrf_score,
            "rank": rank + 1,
        })
    return results


def reciprocal_rank_fusion(
    vector_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
    k: int = 60,
    top_k: int = 50,
) -> List[Dict[str, Any]]:
    """Backward-compatible 2-list RRF (vector + BM25).

    Thin wrapper over :func:`reciprocal_rank_fusion_multi` so existing
    callers keep working.
    """
    return reciprocal_rank_fusion_multi(
        [vector_results, bm25_results], k=k, top_k=top_k,
        labels=["vector", "bm25"],
    )


def deduplicate_results(
    results: List[Dict[str, Any]],
    key: str = "id"
) -> List[Dict[str, Any]]:
    """Remove duplicate results, keeping the first occurrence.

    Falls back to ``chunk_id`` when ``key`` is missing, since graph/Chroma
    chunks use ``chunk_id`` rather than ``id``.
    """
    seen = set()
    unique = []
    for doc in results:
        doc_key = doc.get(key) or doc.get("chunk_id")
        if doc_key and doc_key not in seen:
            seen.add(doc_key)
            unique.append(doc)
    return unique
