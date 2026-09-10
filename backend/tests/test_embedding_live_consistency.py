"""Live vector-consistency gate for the query-embedding batching change.

Batching the query path (1+N embed_single calls → 1 embed_batch call) is
only quality-neutral if the provider returns the SAME vector for a text
whether it is embedded alone or inside a batch. If SiliconFlow ever
introduces per-batch truncation or precision differences, this test catches
it before retrieval quality silently drifts.

Opt-in because it hits the real API (needs a funded SILICON_FLOW_API_KEY):

    KG_LIVE_CONSISTENCY=1 ../.venv/Scripts/python.exe \
        -m pytest tests/test_embedding_live_consistency.py -q

Skipped by default so CI and the offline suite never touch the network.
"""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("KG_LIVE_CONSISTENCY"),
    reason="set KG_LIVE_CONSISTENCY=1 (with a real SILICON_FLOW_API_KEY) to run",
)

_TEXTS = [
    "谁负责知识图谱系统的检索服务？",          # zh, factual
    "How does the hybrid retrieval pipeline fuse BM25 and vector results?",  # en
    "知识图谱 RAG 系统中的实体抽取与规范化流程",  # zh, longer
    "short",                                   # tiny edge case
]


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def test_single_vs_batch_vectors_identical():
    import asyncio

    from app.services.embedding import EmbeddingService

    async def _run():
        svc = EmbeddingService()
        try:
            singles = [
                await svc.embed_single(t, use_cache=False) for t in _TEXTS
            ]
            # embed_batch returns a list of vectors — take [0] for one input.
            batch_of_one = [
                (await svc.embed_batch([t], use_cache=False))[0] for t in _TEXTS
            ]
            batch_all = await svc.embed_batch(_TEXTS, use_cache=False)
        finally:
            await svc.close()
        return singles, batch_of_one, batch_all

    singles, batch_of_one, batch_all = asyncio.run(_run())

    for text, single, solo_batch in zip(_TEXTS, singles, batch_of_one):
        assert len(single) == len(solo_batch)
        assert _cosine(single, solo_batch) >= 0.999, (
            f"single vs batch-of-one diverged for {text!r}"
        )

    for text, single, in_batch in zip(_TEXTS, singles, batch_all):
        assert len(single) == len(in_batch)
        assert _cosine(single, in_batch) >= 0.999, (
            f"single vs 4-text-batch diverged for {text!r}"
        )
