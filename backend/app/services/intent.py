"""LLM-based query intent classification.

Routes a query to one of:
  - fact_retrieval: needs RAG (search the knowledge base)
  - chitchat: greeting / small talk / meta -> answer directly, skip retrieval
  - should_reject: off-topic / opinion / unsafe -> refuse with a template

Classification is best-effort: any failure (timeout, parse error, no API
key, feature disabled) falls back to fact_retrieval so the retrieval
pipeline always runs and the user is never blocked by a classification
glitch.
"""
import asyncio
import json
import logging
import re
from typing import Dict, Any

from app.config import get_settings
from app.services.llm import get_llm_service

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Classify the user's query for a knowledge-graph RAG assistant.
Return ONLY a JSON object: {"intent": "fact_retrieval" | "chitchat" | "should_reject", "reason": "one short clause"}
- fact_retrieval: asks about facts / definitions / relationships retrievable from documents.
- chitchat: greetings, small talk, meta questions about the assistant itself, "thanks", "who are you".
- should_reject: opinions / advice / personal feelings / unsafe or out-of-scope requests that cannot be grounded in the knowledge base and aren't simple chitchat."""

_VALID = ("fact_retrieval", "chitchat", "should_reject")
_FALLBACK: Dict[str, Any] = {"intent": "fact_retrieval", "reason": "classification fallback"}


async def classify_intent(query: str) -> Dict[str, Any]:
    """Classify ``query`` into an intent. Always returns a dict (never raises).

    Falls back to fact_retrieval on any error so the main pipeline is never
    blocked by a classification failure.
    """
    settings = get_settings()
    if not settings.ENABLE_INTENT_ROUTING:
        return _FALLBACK
    try:
        llm = await get_llm_service()
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": query[:1000]},
        ]
        raw = await asyncio.wait_for(
            llm.chat_complete(messages, temperature=0.0, max_tokens=64),
            timeout=settings.INTENT_CLASSIFY_TIMEOUT,
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return _FALLBACK
        parsed = json.loads(m.group(0))
        intent = parsed.get("intent")
        if intent not in _VALID:
            return _FALLBACK
        return {"intent": intent, "reason": parsed.get("reason", "")}
    except Exception as e:
        logger.warning("intent classification failed, falling back: %s", e)
        return _FALLBACK
