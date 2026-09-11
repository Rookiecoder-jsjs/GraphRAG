"""Debug collection for the retrieval pipeline (FEAT-024).

A tiny, dependency-free snapshot builder. ``_retrieve_uncached`` creates one
``DebugCollector`` per debug run and feeds it stage payloads as the pipeline
proceeds; ``finish()`` returns the dict that rides on top of the regular
retrieval result under the ``"debug"`` key.

Two invariants the retrieval layer depends on:

* **Snapshots only.** Pipeline chunk dicts are mutable and (for the final
  result) shared with the caller; ``snap()`` copies scalar fields out and
  never holds a reference. (``_add`` even mutates chunks in place with
  ``setdefault("chunk_id", ...)`` — holding those dicts would alias that.)
* **Bounded.** Channel hit lists are truncated and content previews clipped
  so a recall_k-sized channel cannot blow up the payload.
"""
from typing import Any, Dict, List

_PREVIEW_LIMIT = 160
_MAX_CHANNEL_HITS = 25


def preview(content: str, limit: int = _PREVIEW_LIMIT) -> str:
    """First ``limit`` characters of ``content`` (None-safe)."""
    return (content or "")[:limit]


class DebugCollector:
    """Per-request debug snapshot builder.

    Created only when ``debug=True``; call sites guard with
    ``if debug_out is not None`` so the production path pays nothing beyond
    one None check per stage.
    """

    def __init__(self) -> None:
        self._stages: Dict[str, Any] = {}

    def stage(self, key: str, payload: Any) -> None:
        """Record one named stage payload (last write wins)."""
        self._stages[key] = payload

    def snap(self, chunk: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
        """Scalar-field snapshot of a pipeline chunk dict.

        Normalises the two id conventions (vector/graph chunks carry
        ``chunk_id``, BM25 hits carry ``id``) and pulls the document_id up
        from metadata so the API layer can build its title map uniformly.
        Optional metric keys are kept only when present.
        """
        meta = chunk.get("metadata") or {}
        out: Dict[str, Any] = {
            "chunk_id": chunk.get("chunk_id") or chunk.get("id"),
            "preview": preview(chunk.get("content")),
            "document_id": meta.get("document_id"),
        }
        for key in ("score", "distance", "rrf_score", "rank", "relevance_score"):
            if chunk.get(key) is not None:
                out[key] = chunk[key]
        if chunk.get("sources"):
            out["sources"] = list(chunk["sources"])
        out.update(extra)
        return out

    def channel(self, label: str, kind: str, hits: List[Dict[str, Any]]) -> None:
        """Record one recall channel: its kind (vector/bm25/graph) plus a
        truncated list of hit snapshots."""
        self._stages.setdefault("channels", []).append({
            "label": label,
            "kind": kind,
            "hits": [self.snap(h) for h in hits[:_MAX_CHANNEL_HITS]],
        })

    def finish(self) -> Dict[str, Any]:
        """Return the collected stages (the ``debug`` response value)."""
        return self._stages
