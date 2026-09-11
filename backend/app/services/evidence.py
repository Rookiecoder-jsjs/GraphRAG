"""Evidence grading for RAG answers (FEAT-027, CRAG-lite).

CRAG's insight, reduced to what this pipeline can act on for free: the
cross-encoder rerank already produces a relevance_score for every expanded
chunk, and when ALL of them sit below a floor, generating anyway is how
hallucinations happen. The chat layer consults this grader after retrieval
and, on a "low" verdict, answers with the static insufficient-evidence
template instead of calling the LLM.

Deliberately NOT in this version: a corrective second retrieval. The
pipeline already rewrites + multi-queries on every pass, so an immediate
re-run buys little at full price. If the floor misfires in practice, the
config switch (ENABLE_EVIDENCE_GUARD / EVIDENCE_FLOOR) turns the guard off.

"unknown" is a load-bearing state: a rerank failure makes the reranker
return input order with NO scores — grading that as "low" would break chat
for every provider hiccup. Unknown means "behave exactly as before".
"""
from typing import Any, Dict, List, Tuple


def grade_evidence(chunks: List[Dict[str, Any]], floor: float) -> Tuple[str, float]:
    """Grade retrieved evidence by its reranker relevance scores.

    Returns ``(grade, max_score)``:

    * ``("low", max)``     — chunks exist and every relevance_score sits
      strictly below ``floor`` (empty chunks count as low: nothing to
      ground on). The caller should answer with the insufficient-evidence
      template.
    * ``("normal", max)``  — at least one chunk scores at/above floor.
    * ``("unknown", 0.0)`` — chunks exist but none carries a numeric
      relevance_score (rerank fell back after a failure). Not actionable;
      the caller must keep today's behaviour.
    """
    if not chunks:
        return ("low", 0.0)
    scores = [
        float(c["relevance_score"])
        for c in chunks
        if isinstance(c.get("relevance_score"), (int, float))
        and not isinstance(c.get("relevance_score"), bool)
    ]
    if not scores:
        return ("unknown", 0.0)
    top = max(scores)
    return ("normal" if top >= floor else "low", top)
