"""Async evaluation runner.

Given a retriever callable and a gold set, runs every case, computes
per-case retrieval metrics, and prints a markdown table + a JSON-shaped
aggregate summary.

A retriever is just:

    async def retrieve(query: str, top_k: int) -> List[str]:
        # Return chunk_ids in ranked order (best first).
        ...

This decouples the harness from the real pipeline so tests can pass a
mock retriever (no Neo4j, no ChromaDB, no LLM API calls) and CI stays
under 1 second.

The `__main__` block wires the retriever to the live pipeline
(`build_rag_context` from app.api.chat) so an end-to-end run is also
possible: `python -m eval.runner --user-id 1`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from .metrics import (
    aggregate,
    hit_at_k,
    keyword_coverage,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)

# A retriever takes a query and returns chunk_ids in rank order.
Retriever = Callable[[str, int], Awaitable[List[str]]]

DEFAULT_K_VALUES = (1, 3, 5, 10)


# ---------- Gold loading ---------------------------------------------------

@dataclass
class GoldCase:
    """One gold evaluation case.

    `expected_chunk_ids` is the ground-truth set of acceptable chunks.
    `expected_keywords` is an alternative (or complementary) signal for
    answer-side scoring — used when chunk_ids aren't practical to collect.
    `metadata` holds any extra fields from the JSON file (tags, difficulty,
    free-form notes) so they show up in reports without us hard-coding them.
    """
    id: str
    query: str
    expected_chunk_ids: List[str] = field(default_factory=list)
    expected_keywords: List[str] = field(default_factory=list)
    difficulty: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


def _case_from_json(path: Path) -> GoldCase:
    """Parse one gold JSON file. Strips `_comment` (free-form notes)."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # Surface the comment as a metadata note so it survives into reports
    # (useful for "this case was added because X" breadcrumbs).
    meta: Dict[str, Any] = {}
    if isinstance(data.get("_comment"), list):
        meta["_comment"] = " ".join(str(x) for x in data["_comment"])
    elif isinstance(data.get("_comment"), str):
        meta["_comment"] = data["_comment"]

    for key in ("difficulty", "tags"):
        if key in data and key not in meta:
            meta[key] = data[key]

    return GoldCase(
        id=data["id"],
        query=data["query"],
        expected_chunk_ids=list(data.get("expected_chunk_ids") or []),
        expected_keywords=list(data.get("expected_keywords") or []),
        difficulty=data.get("difficulty", ""),
        tags=list(data.get("tags") or []),
        metadata=meta,
    )


def load_gold_set(gold_dir: Path) -> List[GoldCase]:
    """Load all gold cases from `gold_dir`. Sorted by id for stable output."""
    gold_dir = Path(gold_dir)
    cases = [_case_from_json(p) for p in sorted(gold_dir.glob("*.json"))]
    return cases


# ---------- Per-case evaluation -------------------------------------------

def compute_chunk_metrics(
    retrieved: Sequence[str],
    gold_ids: Sequence[str],
    k_values: Sequence[int],
) -> Dict[str, float]:
    """Compute every chunk-level metric for one case.

    Skips metrics that have no gold (e.g. refusal cases) — those should
    use `compute_keyword_metrics` instead.
    """
    if not gold_ids:
        return {}
    out: Dict[str, float] = {}
    for k in k_values:
        out[f"hit@{k}"] = hit_at_k(retrieved, gold_ids, k)
        out[f"precision@{k}"] = precision_at_k(retrieved, gold_ids, k)
        out[f"recall@{k}"] = recall_at_k(retrieved, gold_ids, k)
        out[f"ndcg@{k}"] = ndcg_at_k(retrieved, gold_ids, k)
    out["mrr"] = mrr(retrieved, gold_ids)
    return out


def compute_keyword_metrics(
    answer: str,
    keywords: Sequence[str],
) -> Dict[str, float]:
    """Answer-side metric (used when the retriever can't be evaluated)."""
    if not keywords:
        return {}
    return {"keyword_coverage": keyword_coverage(answer, keywords)}


async def evaluate_case(
    case: GoldCase,
    retriever: Retriever,
    k_values: Sequence[int] = DEFAULT_K_VALUES,
    answer_provider: Optional[Callable[[str], Awaitable[str]]] = None,
) -> Dict[str, Any]:
    """Run one gold case through the retriever and return a result row.

    If `answer_provider` is given (a callable that runs the full RAG
    generation for the query), we also score keyword coverage — useful
    for refusal cases that don't have gold chunk IDs.
    """
    top_k = max(k_values)
    retrieved = await retriever(case.query, top_k)

    metrics = compute_chunk_metrics(retrieved, case.expected_chunk_ids, k_values)

    if answer_provider is not None and case.expected_keywords:
        answer = await answer_provider(case.query)
        metrics.update(compute_keyword_metrics(answer, case.expected_keywords))

    return {
        "id": case.id,
        "query": case.query,
        "difficulty": case.difficulty,
        "tags": case.tags,
        "retrieved": list(retrieved),
        "metrics": metrics,
    }


# ---------- Full run + report ---------------------------------------------

async def run_evaluation(
    retriever: Retriever,
    gold_set: Sequence[GoldCase],
    k_values: Sequence[int] = DEFAULT_K_VALUES,
    answer_provider: Optional[Callable[[str], Awaitable[str]]] = None,
) -> Dict[str, Any]:
    """Run every case sequentially. Returns a structured report dict."""
    started = time.time()
    per_case: List[Dict[str, Any]] = []
    for case in gold_set:
        per_case.append(
            await evaluate_case(case, retriever, k_values, answer_provider)
        )
    elapsed = time.time() - started

    # Flatten metrics up one level so `aggregate` can mean across cases.
    flat = [
        {"id": row["id"], **row["metrics"]} for row in per_case
    ]
    summary = aggregate(flat)
    summary["elapsed_seconds"] = round(elapsed, 3)
    summary["total_cases"] = len(per_case)

    return {
        "summary": summary,
        "cases": per_case,
    }


def format_markdown_report(report: Dict[str, Any]) -> str:
    """Render a markdown table for human reading."""
    lines: List[str] = []
    lines.append("# RAG Evaluation Report")
    lines.append("")

    summary = report["summary"]
    lines.append("## Summary")
    lines.append(f"- Cases evaluated: **{summary.get('total_cases', 0)}**")
    lines.append(f"- Elapsed: **{summary.get('elapsed_seconds', '?')}s**")
    lines.append("")
    lines.append("| Metric | Value | N |")
    lines.append("| --- | --- | --- |")
    for key in sorted(summary):
        if key.endswith("_mean"):
            base = key[: -len("_mean")]
            n = summary.get(f"{base}_n", "")
            value = summary[key]
            lines.append(f"| {base} | {value:.3f} | {n} |")
        elif key not in ("elapsed_seconds", "total_cases"):
            lines.append(f"| {key} | {summary[key]} | |")
    lines.append("")

    lines.append("## Per-case results")
    lines.append("")
    lines.append("| ID | Difficulty | Hit@1 | Hit@5 | MRR | nDCG@5 | Tags |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for row in report["cases"]:
        m = row["metrics"]
        hit1 = m.get("hit@1")
        hit5 = m.get("hit@5")
        mrr_v = m.get("mrr")
        ndcg5 = m.get("ndcg@5")
        hit1_s = f"{hit1:.2f}" if isinstance(hit1, (int, float)) else "-"
        hit5_s = f"{hit5:.2f}" if isinstance(hit5, (int, float)) else "-"
        mrr_s = f"{mrr_v:.2f}" if isinstance(mrr_v, (int, float)) else "-"
        ndcg_s = f"{ndcg5:.2f}" if isinstance(ndcg5, (int, float)) else "-"
        lines.append(
            f"| {row['id']} | {row['difficulty'] or '-'} | "
            f"{hit1_s} | {hit5_s} | {mrr_s} | {ndcg_s} | "
            f"{', '.join(row['tags'])} |"
        )
    return "\n".join(lines)


# ---------- Live pipeline wiring (for the __main__ entry) ----------------

async def _build_rag_context_retriever(
    user_id: int, use_graph_rag: bool = False,
) -> Retriever:
    """Wrap `build_rag_context` so it returns ranked chunk_ids.

    Import is deferred to runtime so unit tests can import this module
    without the full app stack.
    """
    from app.api.chat import build_rag_context

    async def _retrieve(query: str, top_k: int) -> List[str]:
        ctx = await build_rag_context(
            query=query,
            user_id=user_id,
            top_k=top_k,
            use_hybrid=True,
            use_query_rewrite=True,
            use_graph_rag=use_graph_rag,
        )
        # `chunks` comes back in rank order from the reranker.
        return [c["chunk_id"] for c in ctx["chunks"] if c.get("chunk_id")]

    return _retrieve


async def _build_rag_answer_provider(user_id: int):
    """Optional: produce the LLM-generated answer for keyword coverage."""
    from app.api.chat import build_rag_context
    from app.services.llm import get_llm_service

    async def _answer(query: str) -> str:
        ctx = await build_rag_context(
            query=query, user_id=user_id, top_k=5,
            use_hybrid=True, use_query_rewrite=True,
        )
        llm = await get_llm_service()
        return await llm.generate_rag_response(
            query=query,
            context_chunks=ctx["chunks"],
            related_entities=ctx["entities"],
            related_relations=ctx["relations"],
        )
    return _answer


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run RAG retrieval evaluation against a gold set."
    )
    p.add_argument(
        "--gold-dir",
        type=Path,
        default=Path(__file__).parent / "gold",
        help="Directory of gold JSON files (default: ./gold).",
    )
    p.add_argument(
        "--user-id",
        type=int,
        default=1,
        help="User ID whose knowledge base to evaluate (default: 1).",
    )
    p.add_argument(
        "--no-llm",
        action="store_true",
        help="Skip the LLM step; only evaluate retrieval (no keyword coverage).",
    )
    p.add_argument(
        "--use-graph-rag",
        action="store_true",
        help="Use the graph-RAG retrieval path: query entities → Neo4j "
             "MENTIONS → hard candidate set, then vector+BM25 to fill.",
    )
    p.add_argument(
        "--k-values",
        type=int,
        nargs="+",
        default=list(DEFAULT_K_VALUES),
        help="K values for hit/precision/recall/ndcg (default: 1 3 5 10).",
    )
    p.add_argument(
        "--markdown",
        action="store_true",
        help="Print a markdown report instead of a flat summary.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print the full JSON report to stdout.",
    )
    return p


async def main_async(argv: Optional[Sequence[str]] = None) -> int:
    args = _build_argparser().parse_args(argv)

    gold_set = load_gold_set(args.gold_dir)
    if not gold_set:
        print(f"No gold cases found in {args.gold_dir}", file=sys.stderr)
        return 1

    retriever = await _build_rag_context_retriever(
        args.user_id, use_graph_rag=args.use_graph_rag,
    )
    answer_provider = None if args.no_llm else await _build_rag_answer_provider(args.user_id)

    report = await run_evaluation(
        retriever=retriever,
        gold_set=gold_set,
        k_values=args.k_values,
        answer_provider=answer_provider,
    )

    if args.json:
        # Strip the `retrieved` list from the per-case rows to keep the
        # JSON compact — it's long and only useful for debugging.
        for row in report["cases"]:
            row.pop("retrieved", None)
        print(json.dumps(report, indent=2, ensure_ascii=False))
    elif args.markdown:
        print(format_markdown_report(report))
    else:
        s = report["summary"]
        print(f"Cases: {s.get('total_cases', 0)}  "
              f"elapsed: {s.get('elapsed_seconds', '?')}s")
        for key in sorted(s):
            if key.endswith("_mean"):
                base = key[: -len("_mean")]
                print(f"  {base:18s}  {s[key]:.3f}")

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
