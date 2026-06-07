"""Tests for the RAG eval harness.

Two ways to run:

1. Standalone (no pytest dependency, recommended for CI smoke-tests):
     cd backend
     ../.venv/Scripts/python.exe eval/test_eval.py

2. With pytest (for richer failure diffs and IDE integration):
     pip install pytest pytest-asyncio
     cd backend
     ../.venv/Scripts/python.exe -m pytest eval/test_eval.py -v

No external services (Neo4j, ChromaDB, LLM) are required — everything
runs against pure functions and an in-process mock retriever.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Make the `app` package importable when this test is run in isolation
# (e.g. via `python eval/test_eval.py` from the backend/ root).
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from eval.metrics import (  # noqa: E402
    aggregate,
    hit_at_k,
    keyword_coverage,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    tokenize,
)
from eval.runner import (  # noqa: E402
    GoldCase,
    _case_from_json,
    compute_chunk_metrics,
    compute_keyword_metrics,
    evaluate_case,
    load_gold_set,
    run_evaluation,
)


# =========================================================================
# Standalone runner (works without pytest)
# =========================================================================

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_failures: list = []


def check(name: str, cond: bool, detail: str = ""):
    status = PASS if cond else FAIL
    suffix = f" — {detail}" if detail and not cond else ""
    print(f"  [{status}] {name}{suffix}")
    if not cond:
        _failures.append(name)


# `pytest.approx` may not be importable when running standalone, so provide
# a minimal shim.
try:
    import pytest  # noqa: F401
    _HAS_PYTEST = True
    _approx = pytest.approx
except ImportError:
    _HAS_PYTEST = False
    def _approx(value, expected, tol=1e-9):
        return abs(value - expected) <= tol


# =========================================================================
# Test cases. Each function returns (name, passed, optional detail).
# The standalone driver and pytest (auto-discovery) both consume them.
# =========================================================================

def test_hit_at_k_hit():
    check("hit@k: gold in top-k returns 1.0",
          hit_at_k(["a", "b", "c"], {"b"}, k=3) == 1.0)


def test_hit_at_k_miss():
    check("hit@k: gold below k returns 0.0",
          hit_at_k(["a", "b", "c"], {"z"}, k=3) == 0.0)


def test_hit_at_k_boundary():
    check("hit@k: rank 4 with k=3 is miss, k=4 is hit",
          hit_at_k(["a", "b", "c", "d"], {"d"}, k=3) == 0.0
          and hit_at_k(["a", "b", "c", "d"], {"d"}, k=4) == 1.0)


def test_hit_at_k_empty():
    check("hit@k: empty gold or retrieved → 0.0",
          hit_at_k(["a"], set(), k=5) == 0.0
          and hit_at_k([], {"a"}, k=5) == 0.0)


def test_mrr_first_position():
    check("mrr: rank 1 = 1.0",
          mrr(["a", "b", "c"], {"a"}) == 1.0)


def test_mrr_second_position():
    check("mrr: rank 2 = 0.5",
          mrr(["x", "a", "b"], {"a"}) == 0.5)


def test_mrr_no_hit():
    check("mrr: no hit → 0.0",
          mrr(["a", "b"], {"z"}) == 0.0)


def test_mrr_first_hit_wins():
    check("mrr: uses earliest hit position",
          mrr(["a", "b", "c"], {"c", "a", "b"}) == 1.0)


def test_precision_perfect():
    check("precision@k: full overlap = 1.0",
          precision_at_k(["a", "b", "c"], {"a", "b", "c"}, 3) == 1.0)


def test_precision_partial():
    check("precision@k: 2 hits in top 4 of 3 gold = 0.5",
          precision_at_k(["a", "b", "x", "y"], {"a", "b", "c"}, 4) == 0.5)


def test_recall_perfect():
    check("recall@k: all gold in top-k = 1.0",
          recall_at_k(["a", "b", "c"], {"a", "b", "c"}, 3) == 1.0)


def test_recall_partial():
    check("recall@k: 2 of 3 gold = ~0.667",
          _approx(recall_at_k(["a", "b", "x", "y"], {"a", "b", "c"}, 4), 2 / 3))


def test_precision_recall_empty_gold():
    check("precision/recall: empty gold → 0.0",
          precision_at_k(["a"], set(), 5) == 0.0
          and recall_at_k(["a"], set(), 5) == 0.0)


def test_ndcg_perfect():
    check("ndcg@k: ideal ordering = 1.0",
          _approx(ndcg_at_k(["a", "b", "c"], {"a", "b", "c"}, 3), 1.0))


def test_ndcg_worst():
    check("ndcg@k: gold at end is < 1.0",
          0.0 < ndcg_at_k(["x", "y", "a"], {"a"}, 3) < 1.0)


def test_ndcg_empty_gold():
    check("ndcg@k: empty gold → 0.0",
          ndcg_at_k(["a", "b"], set(), 5) == 0.0)


def test_keyword_all_present():
    check("keyword_coverage: all present = 1.0",
          keyword_coverage("Paris is the capital of France", ["Paris", "France"]) == 1.0)


def test_keyword_some_missing():
    check("keyword_coverage: half missing = 0.5",
          keyword_coverage("Paris is the capital", ["Paris", "France"]) == 0.5)


def test_keyword_case_insensitive():
    check("keyword_coverage: case-insensitive substring",
          keyword_coverage("PARIS is here", ["paris"]) == 1.0)


def test_keyword_chinese():
    check("keyword_coverage: Chinese substring match",
          keyword_coverage("文档包含硅基流动的信息", ["硅基流动"]) == 1.0)


def test_keyword_empty():
    check("keyword_coverage: empty keywords → 0.0; empty answer → 0.0",
          keyword_coverage("anything", []) == 0.0
          and keyword_coverage("", ["Paris"]) == 0.0)


def test_keyword_blanks_filtered():
    check("keyword_coverage: blank keywords are filtered",
          keyword_coverage("Paris", ["Paris", "", "  "]) == 1.0)


def test_tokenize_english():
    toks = tokenize("Hello, World! Foo bar.")
    check("tokenize: English splits on non-word",
          toks >= {"hello", "world", "foo", "bar"})


def test_tokenize_chinese():
    check("tokenize: Chinese chars kept individually",
          "北" in tokenize("北京是首都") and "京" in tokenize("北京是首都"))


def test_tokenize_empty():
    check("tokenize: empty input → empty set", tokenize("") == set())


def test_aggregate_basic():
    result = aggregate([
        {"hit@5": 1.0, "mrr": 0.5},
        {"hit@5": 0.0, "mrr": 0.25},
    ])
    check("aggregate: hit@5 mean = 0.5",
          _approx(result["hit@5_mean"], 0.5))
    check("aggregate: mrr mean = 0.375",
          _approx(result["mrr_mean"], 0.375))
    check("aggregate: n recorded per metric",
          result["hit@5_n"] == 2 and result["mrr_n"] == 2)


def test_aggregate_missing_keys():
    result = aggregate([{"hit@5": 1.0}, {"mrr": 0.5}])
    check("aggregate: missing key → computed from 1 sample",
          result["hit@5_mean"] == 1.0 and result["hit@5_n"] == 1)
    check("aggregate: independent metrics both present",
          "mrr_mean" in result and "hit@5_mean" in result)


def test_aggregate_empty():
    check("aggregate: empty input → empty output", aggregate([]) == {})


# ---------- Gold loading ----------

def test_load_gold_parses_valid_files(tmp_path):
    (tmp_path / "01_a.json").write_text(json.dumps({
        "id": "a", "query": "Q1",
        "expected_chunk_ids": ["c1"], "expected_keywords": ["k1"],
        "difficulty": "easy", "tags": ["factual"],
    }))
    (tmp_path / "02_b.json").write_text(json.dumps({
        "id": "b", "query": "Q2",
        "expected_chunk_ids": [], "expected_keywords": [],
    }))
    (tmp_path / "README.txt").write_text("not a case")

    cases = load_gold_set(tmp_path)
    check("load_gold_set: only .json files parsed",
          len(cases) == 2)
    check("load_gold_set: sorted by id",
          [c.id for c in cases] == ["a", "b"])
    check("load_gold_set: chunk_ids + tags preserved",
          cases[0].expected_chunk_ids == ["c1"]
          and cases[0].tags == ["factual"])


def test_load_gold_strips_comment(tmp_path):
    (tmp_path / "01_x.json").write_text(json.dumps({
        "id": "x", "query": "Q",
        "_comment": ["line 1", "line 2"],
        "expected_chunk_ids": [],
    }))
    case = _case_from_json(tmp_path / "01_x.json")
    check("load_gold_set: _comment collapsed into metadata",
          "line 1 line 2" in case.metadata["_comment"])


def test_load_gold_missing_id_raises(tmp_path):
    (tmp_path / "01_x.json").write_text(json.dumps({"query": "Q"}))
    raised = False
    try:
        load_gold_set(tmp_path)
    except KeyError:
        raised = True
    check("load_gold_set: missing 'id' raises KeyError", raised)


# ---------- Runner ----------

def _mock_retriever(retrieved_lists):
    async def _retrieve(query, top_k):
        return list(retrieved_lists.get(query, []))[:top_k]
    return _retrieve


def test_compute_chunk_metrics_full_match():
    m = compute_chunk_metrics(["a", "b", "c"], ["a", "b"], k_values=(1, 3, 5))
    check("compute_chunk_metrics: full match",
          m["hit@1"] == 1.0 and m["hit@3"] == 1.0 and m["recall@3"] == 1.0)


def test_compute_chunk_metrics_no_gold():
    m = compute_chunk_metrics(["a", "b"], [], k_values=(1, 3))
    check("compute_chunk_metrics: empty gold → no metrics", m == {})


def test_compute_keyword_metrics_present():
    m = compute_keyword_metrics("Paris is in France", ["Paris", "London"])
    check("compute_keyword_metrics: 1 of 2 keywords = 0.5", m["keyword_coverage"] == 0.5)


def test_compute_keyword_metrics_no_keywords():
    check("compute_keyword_metrics: empty keywords → empty dict",
          compute_keyword_metrics("anything", []) == {})


def test_evaluate_case_basic():
    case = GoldCase(
        id="c1", query="Q",
        expected_chunk_ids=["b", "c"],
        expected_keywords=["foo"],
    )
    retriever = _mock_retriever({"Q": ["a", "b", "c", "x"]})
    result = asyncio.run(evaluate_case(case, retriever, k_values=(1, 3, 5)))
    check("evaluate_case: returns id and retrieved list",
          result["id"] == "c1" and result["retrieved"] == ["a", "b", "c", "x"])
    check("evaluate_case: mrr computed from first hit",
          result["metrics"]["mrr"] == 0.5)
    check("evaluate_case: hit@1 / hit@3 differ for rank 2 hit",
          result["metrics"]["hit@1"] == 0.0
          and result["metrics"]["hit@3"] == 1.0)
    check("evaluate_case: keyword_coverage NOT computed without provider",
          "keyword_coverage" not in result["metrics"])


def test_evaluate_case_with_answer_provider():
    case = GoldCase(
        id="c1", query="Q",
        expected_chunk_ids=["b"],
        expected_keywords=["Paris"],
    )
    retriever = _mock_retriever({"Q": ["a", "b"]})

    async def _answer(query):
        return "Paris is great"

    result = asyncio.run(evaluate_case(
        case, retriever, k_values=(1, 3), answer_provider=_answer,
    ))
    check("evaluate_case: keyword_coverage computed when provider given",
          result["metrics"]["keyword_coverage"] == 1.0)


def test_run_evaluation_aggregation():
    gold = [
        GoldCase(id="a", query="Q1", expected_chunk_ids=["x"]),
        GoldCase(id="b", query="Q2", expected_chunk_ids=["y"]),
    ]
    retriever = _mock_retriever({
        "Q1": ["x", "b", "c"],   # hit@1 = 1
        "Q2": ["a", "y", "d"],   # hit@1 = 0, hit@2 = 1
    })
    report = asyncio.run(run_evaluation(
        retriever, gold, k_values=(1, 3, 5),
    ))
    check("run_evaluation: total_cases counted",
          report["summary"]["total_cases"] == 2)
    check("run_evaluation: hit@1 mean = 0.5 (one hit, one miss)",
          _approx(report["summary"]["hit@1_mean"], 0.5))
    check("run_evaluation: hit@3 mean = 1.0 (both within top 3)",
          _approx(report["summary"]["hit@3_mean"], 1.0))
    check("run_evaluation: cases preserve full retrieved lists",
          report["cases"][0]["retrieved"][0] == "x")


def test_run_evaluation_refusal_path():
    """Refusal cases (no chunk_ids) should still be scored via keyword coverage."""
    gold = [
        GoldCase(id="refuse", query="Mars pop?", expected_keywords=["no information"]),
    ]
    retriever = _mock_retriever({"Mars pop?": []})

    async def _answer(query):
        return "I have no information about that."

    report = asyncio.run(run_evaluation(
        retriever, gold, k_values=(1, 3), answer_provider=_answer,
    ))
    metrics = report["cases"][0]["metrics"]
    check("refusal case: no chunk-level metrics",
          "hit@3" not in metrics)
    check("refusal case: keyword_coverage computed",
          metrics.get("keyword_coverage") == 1.0)
    check("refusal case: summary aggregates keyword_coverage",
          "keyword_coverage_mean" in report["summary"])


# =========================================================================
# Driver
# =========================================================================

ALL_TESTS = [
    test_hit_at_k_hit,
    test_hit_at_k_miss,
    test_hit_at_k_boundary,
    test_hit_at_k_empty,
    test_mrr_first_position,
    test_mrr_second_position,
    test_mrr_no_hit,
    test_mrr_first_hit_wins,
    test_precision_perfect,
    test_precision_partial,
    test_recall_perfect,
    test_recall_partial,
    test_precision_recall_empty_gold,
    test_ndcg_perfect,
    test_ndcg_worst,
    test_ndcg_empty_gold,
    test_keyword_all_present,
    test_keyword_some_missing,
    test_keyword_case_insensitive,
    test_keyword_chinese,
    test_keyword_empty,
    test_keyword_blanks_filtered,
    test_tokenize_english,
    test_tokenize_chinese,
    test_tokenize_empty,
    test_aggregate_basic,
    test_aggregate_missing_keys,
    test_aggregate_empty,
    test_load_gold_parses_valid_files,
    test_load_gold_strips_comment,
    test_load_gold_missing_id_raises,
    test_compute_chunk_metrics_full_match,
    test_compute_chunk_metrics_no_gold,
    test_compute_keyword_metrics_present,
    test_compute_keyword_metrics_no_keywords,
    test_evaluate_case_basic,
    test_evaluate_case_with_answer_provider,
    test_run_evaluation_aggregation,
    test_run_evaluation_refusal_path,
]


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks...")
    for fn in ALL_TESTS:
        # Some tests need pytest's tmp_path fixture. When running standalone
        # without pytest, fall back to a temporary directory.
        try:
            fn()
        except TypeError as e:
            if "tmp_path" in str(e) and not _HAS_PYTEST:
                # Use stdlib tmp_path stand-in
                import tempfile
                with tempfile.TemporaryDirectory() as td:
                    tmp_path = Path(td)
                    fn(tmp_path)
            else:
                raise

    print()
    if _failures:
        print(f"{FAIL} {len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print(f"{PASS} All checks passed ({len(ALL_TESTS)} tests).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
