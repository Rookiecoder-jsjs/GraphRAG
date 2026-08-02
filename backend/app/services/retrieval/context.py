"""RetrievalContext — the value object flowing through the pipeline."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RetrievalContext:
    """Inputs + intermediate results for one retrieval run.

    Steps read the fields they need and write their outputs; the wrapper
    (chat.py / search.py) sets the input knobs from request/settings.
    """

    # --- inputs (set by the caller) ---
    query: str
    user_id: int
    settings: Any  # get_settings() result
    top_k: int = 5
    include_context: bool = True
    use_hybrid: bool = True
    use_query_rewrite: bool = True
    use_graph_rag: bool = False
    # Threaded for downstream prompt building; no step reads it today.
    compare_mode: bool = False
    # Recall per retriever: chat = RERANK_RECALL_K; search = top_k * 4.
    vector_recall: int = 25
    bm25_recall: int = 25
    # Entity enrichment knobs: chat = (3, 2, append); search = (5, 1, none).
    entity_name_limit: int = 3
    entity_depth: int = 2
    append_related: bool = True
    # Search dedups context neighbors (two-phase); chat appends as-is.
    dedup_context: bool = False

    # --- produced by steps ---
    search_query: str = ""
    query_embedding: List[float] = field(default_factory=list)
    vector_results: List[Dict[str, Any]] = field(default_factory=list)
    bm25_results: List[Dict[str, Any]] = field(default_factory=list)
    graph_results: List[Dict[str, Any]] = field(default_factory=list)
    fused: Optional[List[Dict[str, Any]]] = None
    reranked_chunks: List[Dict[str, Any]] = field(default_factory=list)
    chunks: List[Dict[str, Any]] = field(default_factory=list)
    entities: List[Dict[str, Any]] = field(default_factory=list)
    relations: List[Dict[str, Any]] = field(default_factory=list)
    timings: Dict[str, float] = field(default_factory=dict)
