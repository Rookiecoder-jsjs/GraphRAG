"""Global concurrency gate for document-ingestion pipelines.

The upload endpoint spawns one background pipeline per document (Starlette
BackgroundTasks). Each pipeline is expensive — embedding + LLM entity
extraction + Neo4j/Chroma/SQLite writes — and is only throttled *inside*
providers (embedding Semaphore(5), extraction concurrency 20), so bursts of
concurrent uploads can fire dozens of pipelines at once and trip provider
rate limits / SQLite write contention.

This module serializes the pipelines themselves: ``DOC_INGEST_CONCURRENCY``
run at once, the rest wait on the gate. While waiting, a document keeps its
``pending`` status and emits no progress, so the UI stays truthful. The gate
is per-process: it bounds the pipelines one uvicorn worker can run.
"""
import asyncio
from typing import Optional

from app.config import get_settings

_gate: Optional[asyncio.Semaphore] = None


def get_ingest_gate() -> asyncio.Semaphore:
    """Return the process-wide ingest gate (lazily built from config)."""
    global _gate
    if _gate is None:
        _gate = asyncio.Semaphore(get_settings().DOC_INGEST_CONCURRENCY)
    return _gate


def reset_ingest_gate() -> None:
    """Drop the cached gate so the next ``get_ingest_gate`` rebuilds it.

    Tests use this to size the gate (or clear state between cases).
    """
    global _gate
    _gate = None
