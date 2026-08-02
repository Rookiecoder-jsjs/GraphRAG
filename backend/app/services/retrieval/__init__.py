"""Composable retrieval pipeline.

chat.py and search.py declare their retrieval as a list of named steps
(config: CHAT_PIPELINE / SEARCH_PIPELINE); the runner executes them in
order, threading a :class:`RetrievalContext` between steps.
"""
from app.services.retrieval.context import RetrievalContext
from app.services.retrieval.pipeline import (
    build_pipeline,
    parse_pipeline,
    run_pipeline,
)
from app.services.retrieval.steps import STEP_REGISTRY

__all__ = [
    "RetrievalContext",
    "STEP_REGISTRY",
    "build_pipeline",
    "parse_pipeline",
    "run_pipeline",
]
