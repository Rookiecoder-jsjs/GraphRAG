"""LLM-as-judge: answer-side (generation) metrics for RAG evaluation.

The retrieval harness (runner.py) measures "did we find the right chunks".
This module measures "did the model answer faithfully from those chunks" —
the second half of the RAG eval story:

  - faithfulness        fraction of the answer's factual claims that are
                        supported by the retrieved context (0-1)
  - hallucination_rate  1 - faithfulness — claims the model invented
  - answer_relevance    whether the answer addresses the query (0-1)
  - citation_accuracy   whether the answer's [N] citations are backed by
                        the retrieved context (0-1)
  - answer_correctness  whether the answer is factually right vs the gold
                        expected_answer / expected_keywords (0-1)

One LLM call per case returns a structured JSON verdict; if parsing fails
we degrade gracefully to the cheap keyword_coverage fallback so a judge
blip never crashes the whole run. Judge calls disable thinking (fast
JSON, no reasoning trace) — same convention as retrieval preprocessing.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from app.prompts import load_prompt
from .metrics import keyword_coverage

logger = logging.getLogger(__name__)

# Keep the context fed to the judge bounded — the judge only needs enough
# text to verify claims, and huge payloads cost tokens without adding signal.
_MAX_CONTEXT_CHARS = 800

# Judge prompt v2 (confidence fields + executable criteria). Text lives in
# app/prompts/templates/judge.md (GUIDE-003 T1-1 + T2-1).
_JUDGE_SYSTEM_PROMPT = load_prompt("judge")

_JUDGE_SYSTEM_PROMPT = """你是一个严格、客观的 RAG 质量评估员。你会收到：
- 用户问题（query）
- 系统生成的回答（answer）
- 检索到的资料片段（context，来自知识库）
- 期望答案要点（expected_answer，可能为空）

## 输出格式（必须严格匹配，MUST MATCH exactly）

只输出一个 JSON 对象，不要输出任何其他文字（无 markdown 围栏、无解释）：
{
  "claims": [
    {"claim": "回答中的一个事实性陈述", "supported": true或false, "confidence": 0.0到1.0的小数},
    ...
  ],
  "answer_relevance": 0.0到1.0的小数,
  "citation_accuracy": 0.0到1.0的小数,
  "answer_correctness": 0.0到1.0的小数,
  "overall_confidence": 0.0到1.0的小数,
  "notes": "一句话说明你的主要判断依据"
}

## 判定规则（可执行判据）

claims：把回答拆成独立的事实性陈述（不含连接词、不含"我认为"等立场表述）。逐条判断：
- supported=true 当且仅当 context 中存在能直接推出该陈述的原文；需要借助外部知识补全才能成立的陈述，supported=false。
- 数字、日期、专有名词必须与 context 逐字一致才算 supported；近似表述视为不一致。
- 每个 claim 附 confidence（你对这条判断本身的把握）。不确定时降低 confidence，而不是猜测 supported。
- 如果 context 为空或回答明确拒绝回答，claims 应为 []（无陈述可评估）。

citation_accuracy：回答中的 [N] 引用标记判定——[N] 所指片段确实包含该陈述所依据的内容 → 达标；指错片段或所指片段不含该内容 → 计入失准。若回答没有引用标记，给 1.0（无引用可扣分）。

answer_relevance：回答是否切题、是否直接回应了用户问题。完全离题给 0，完全切题给 1。

answer_correctness：对照 expected_answer 判断回答是否事实正确、要点是否覆盖。若 expected_answer 为空，则结合 context 判断。若这是拒答场景（expected_answer 表示应拒绝），回答明确拒答给高分、编造内容给 0。

overall_confidence：你对本次整体评估的把握程度。context 片段过短、被截断、或回答含糊时应当调低。

## 元规则

- 不要因为回答流畅、礼貌、结构清晰而放松事实判断——文采不是正确性。
- 不要为了显得严格而给出资料不支持的扣分——没有确定的问题就不要制造问题。
- 只依据给出的资料判断，不要用自己的知识补充或猜测。
- notes 只写一句话主依据，不要罗列细节。"""


def _clip(text: str, limit: int = _MAX_CONTEXT_CHARS) -> str:
    """Truncate long text with a marker so the judge stays bounded."""
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…[截断]"


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first JSON object out of an LLM response.

    Mirrors the JSON-extraction convention in app/services/query_processor.py
    (regex scan + json.loads with a tolerant fallback).
    """
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _score_from_claims(claims: Any) -> Optional[float]:
    """Fraction of claims judged supported. None when claims are unusable."""
    if not isinstance(claims, list) or not claims:
        return None
    supported = 0
    total = 0
    for item in claims:
        if not isinstance(item, dict):
            continue
        supported_ = item.get("supported")
        if isinstance(supported_, bool):
            total += 1
            supported += 1 if supported_ else 0
    return (supported / total) if total else None


# Per-claim confidence values that are missing/invalid degrade to this level
# rather than being dropped — a v1-shaped (confidence-less) judge response
# must not crash parsing and must not silently read as high-confidence.
_DEFAULT_CLAIM_CONFIDENCE = 0.5


def _mean_claim_confidence(claims: Any) -> Optional[float]:
    """Mean of per-claim confidence values; None when there are no claims."""
    if not isinstance(claims, list) or not claims:
        return None
    values: List[float] = []
    for item in claims:
        if not isinstance(item, dict):
            continue
        value = _to_float(item.get("confidence"))
        values.append(value if value is not None else _DEFAULT_CLAIM_CONFIDENCE)
    return round(sum(values) / len(values), 4) if values else None


def _to_float(value: Any) -> Optional[float]:
    """Normalize a judge number (may come back as str/int/float) to [0,1]."""
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, f))


async def judge(
    query: str,
    answer: str,
    context_chunks: List[Dict[str, Any]],
    expected_answer: str = "",
    expected_keywords: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Score one RAG answer with an LLM judge. Returns a metrics dict.

    Falls back to keyword_coverage for answer_correctness when the judge
    response can't be parsed — the run must never crash on a judge blip.
    """
    from app.services.llm import get_llm_service

    context_text = "\n\n".join(
        f"[片段{i + 1}] {_clip(c.get('content') or c.get('text') or '')}"
        for i, c in enumerate(context_chunks)
    ) or "（无检索资料）"

    prompt = (
        f"用户问题：{query}\n\n"
        f"系统回答：\n{_clip(answer, 2000)}\n\n"
        f"检索到的资料：\n{context_text}\n\n"
        f"期望答案要点：{_clip(expected_answer, 1000) or '（未提供）'}"
    )

    try:
        llm = await get_llm_service()
        raw = await llm.chat_complete(
            [
                {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=1500,
            enable_thinking=False,
        )
    except Exception as e:  # noqa: BLE001 - judge must never take down a run
        logger.warning("judge LLM call failed: %s", e)
        return _fallback_metrics(answer, expected_keywords)

    data = _extract_json_object(raw)
    if data is None:
        logger.warning("judge response unparseable; falling back to keyword coverage")
        return _fallback_metrics(answer, expected_keywords)

    faithfulness = _score_from_claims(data.get("claims"))
    metrics: Dict[str, Any] = {}
    if faithfulness is not None:
        metrics["faithfulness"] = faithfulness
        metrics["hallucination_rate"] = round(1.0 - faithfulness, 4)
    for key in ("answer_relevance", "citation_accuracy", "answer_correctness"):
        value = _to_float(data.get(key))
        if value is not None:
            metrics[key] = value

    # Judge self-reported confidence (GUIDE-003 T1-1): overall first, falling
    # back to the mean of per-claim confidences. Absent from the fallback
    # path by design — a missing field downstream means "low confidence".
    overall_confidence = _to_float(data.get("overall_confidence"))
    if overall_confidence is None:
        overall_confidence = _mean_claim_confidence(data.get("claims"))
    if overall_confidence is not None:
        metrics["judge_confidence"] = overall_confidence

    # answer_correctness absent in judge output? Fall back to keyword coverage.
    if "answer_correctness" not in metrics and expected_keywords:
        metrics["answer_correctness"] = keyword_coverage(answer, expected_keywords)

    return metrics


def _fallback_metrics(answer: str, expected_keywords: Optional[List[str]]) -> Dict[str, Any]:
    """Cheap degradation when the judge can't be reached or parsed."""
    metrics: Dict[str, Any] = {}
    if expected_keywords:
        metrics["answer_correctness"] = keyword_coverage(answer, expected_keywords)
    return metrics
