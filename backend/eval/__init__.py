"""RAG evaluation harness.

Provides retrieval-quality metrics, a gold-set schema, and an async runner
that can plug into the real pipeline (build_rag_context) or a mock retriever
for fast CI.

See README.md in this directory for usage and how to add new gold cases.
"""
