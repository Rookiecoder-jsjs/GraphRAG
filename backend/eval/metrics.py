"""Retrieval-quality metrics.

All functions are pure: given a ranked list of retrieved chunk IDs and a set
of gold chunk IDs, return a scalar score. They are intentionally decoupled
from any retrieval backend so they can be unit-tested without infra and
reused for any future RAG variant (graph-rag, hybrid, etc.).

Conventions:
  - `retrieved` is a list ordered from best (rank 1) to worst.
  - `gold` is a set / list of acceptable chunk IDs. Order is irrelevant
    (a chunk is either relevant or it isn't).
  - All scores are floats in [0, 1]. Higher is better.
  - Empty `gold` returns 0.0 for retrieval metrics (we don't know what's
    correct). Empty `retrieved` returns 0.0 for everything except
    `keyword_coverage`.
"""
from __future__ import annotations

import math
import re
from typing import Iterable, Sequence, Set


# ---------- Set-based metrics (order doesn't matter) -----------------------

def precision_at_k(
    retrieved: Sequence[str], gold: Iterable[str], k: int
) -> float:
    """Fraction of the top-k retrieved items that are in gold.

    precision@k = |retrieved[:k] ∩ gold| / k
    """
    if k <= 0:
        return 0.0
    top_k = list(retrieved[:k])
    gold_set = set(gold)
    if not top_k:
        return 0.0
    hits = sum(1 for c in top_k if c in gold_set)
    return hits / k


def recall_at_k(
    retrieved: Sequence[str], gold: Iterable[str], k: int
) -> float:
    """Fraction of gold items found anywhere in the top-k.

    recall@k = |retrieved[:k] ∩ gold| / |gold|
    """
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    top_k = set(retrieved[:k])
    hits = len(top_k & gold_set)
    return hits / len(gold_set)


# ---------- Rank-aware metrics ----------------------------------------------

def hit_at_k(retrieved: Sequence[str], gold: Iterable[str], k: int) -> float:
    """1.0 if any gold item appears in the top-k, else 0.0.

    Cheapest sanity check — answers "did we surface the right thing at all?".
    """
    gold_set = set(gold)
    return 1.0 if any(c in gold_set for c in retrieved[:k]) else 0.0


def mrr(retrieved: Sequence[str], gold: Iterable[str]) -> float:
    """Mean Reciprocal Rank — 1 / rank of the first gold item, 0 if none.

    Useful when the user typically only acts on the top result.
    """
    gold_set = set(gold)
    for i, cid in enumerate(retrieved, start=1):
        if cid in gold_set:
            return 1.0 / i
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], gold: Iterable[str], k: int) -> float:
    """Normalized Discounted Cumulative Gain.

    Binary relevance (1 if in gold, 0 otherwise). This is the most
    informative single-number summary of a ranked list when you care
    about both recall and rank.

    nDCG@k = DCG@k / IDCG@k
    where DCG@k = sum_{i=1..k} rel_i / log2(i + 1)
    """
    gold_set = set(gold)
    if not gold_set:
        return 0.0
    dcg = 0.0
    for i, cid in enumerate(retrieved[:k], start=1):
        if cid in gold_set:
            dcg += 1.0 / math.log2(i + 1)
    # IDCG: same gold set, all in best positions
    ideal_hits = min(len(gold_set), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    if idcg == 0.0:
        return 0.0
    return dcg / idcg


# ---------- Answer-side fallback (no chunk_ids required) -------------------

# Match CJK characters individually OR ASCII words as a whole.
# Order matters: put CJK first because Python's \w includes CJK in
# Unicode mode, so leaving \w+ on the left would swallow the CJK
# characters into a single token (which is not what we want).
_TOKEN_RE = re.compile(r"[一-鿿]|[A-Za-z0-9_]+", re.UNICODE)


def _normalize(text: str) -> str:
    return text.lower()


def tokenize(text: str) -> Set[str]:
    """Lowercase + word/char tokens; Chinese chars are kept individually.

    Used for cheap keyword coverage when we don't have ground-truth chunk IDs.
    """
    return {m.group(0) for m in _TOKEN_RE.finditer(_normalize(text or ""))}


def keyword_coverage(answer: str, keywords: Iterable[str]) -> float:
    """Fraction of `keywords` (case-insensitive) that appear as substrings
    in `answer`.

    This is a cheap proxy for answer quality when you don't want to set up
    gold chunk IDs. A keyword present in the model's answer contributes 1;
    a missing keyword contributes 0. Returns 0.0 if `keywords` is empty.

    Note: substring match is intentionally forgiving — it catches Chinese
    terms that wouldn't survive whitespace tokenization.
    """
    kws = [k.strip() for k in keywords if k and k.strip()]
    if not kws:
        return 0.0
    text = _normalize(answer or "")
    hits = sum(1 for kw in kws if _normalize(kw) in text)
    return hits / len(kws)


# ---------- Aggregation ----------------------------------------------------

def aggregate(
    per_case: Sequence[dict],
) -> dict:
    """Mean-aggregate a list of per-case metric dicts into a single report.

    Skips metrics that aren't present in a case (e.g. keyword_coverage when
    the case has no keywords) so missing fields don't drag the average down.
    Also skips any non-numeric values defensively, so a stray string field
    (like an `id` accidentally passed in) doesn't blow up the summary.
    """
    keys: Set[str] = set()
    for case in per_case:
        keys.update(case.keys())

    report: dict = {}
    for key in sorted(keys):
        # Only consider numeric values — guards against accidentally
        # passing through case-level metadata (id, query, etc.).
        values = [
            c[key] for c in per_case
            if key in c and isinstance(c[key], (int, float)) and not isinstance(c[key], bool)
        ]
        if not values:
            continue
        report[f"{key}_mean"] = sum(values) / len(values)
        report[f"{key}_n"] = len(values)
    return report
