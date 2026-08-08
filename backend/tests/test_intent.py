"""Tests for LLM-based intent classification."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from unittest import mock

_BACKEND = Path(__file__).resolve().parent.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

os.environ.setdefault("JWT_SECRET", "test-only-" + "k" * 48)
os.environ.setdefault("APP_ENV", "test")

from app.services import intent  # noqa: E402


class _FakeLLM:
    """Stand-in for LLMService returning a canned chat_complete string."""
    def __init__(self, response: str):
        self._r = response

    async def chat_complete(self, messages, **kw):
        return self._r


class _BoomLLM:
    async def chat_complete(self, *a, **k):
        raise RuntimeError("boom")


def _run(coro):
    return asyncio.run(coro)


def _patch_llm(response: str):
    return mock.patch.object(
        intent, "get_llm_service", mock.AsyncMock(return_value=_FakeLLM(response))
    )


def test_classify_fact_retrieval():
    with _patch_llm('{"intent":"fact_retrieval","reason":"asks a fact"}'):
        r = _run(intent.classify_intent("What is photosynthesis?"))
    assert r["intent"] == "fact_retrieval"


def test_classify_chitchat():
    with _patch_llm('{"intent":"chitchat","reason":"greeting"}'):
        r = _run(intent.classify_intent("hi there"))
    assert r["intent"] == "chitchat"


def test_classify_should_reject():
    with _patch_llm('{"intent":"should_reject","reason":"opinion"}'):
        r = _run(intent.classify_intent("what do you think about politics?"))
    assert r["intent"] == "should_reject"


def test_classify_fallback_on_bad_json():
    with _patch_llm("not json at all"):
        r = _run(intent.classify_intent("anything"))
    assert r["intent"] == "fact_retrieval"


def test_classify_fallback_on_exception():
    with mock.patch.object(
        intent, "get_llm_service", mock.AsyncMock(return_value=_BoomLLM())
    ):
        r = _run(intent.classify_intent("anything"))
    assert r["intent"] == "fact_retrieval"


def test_classify_disabled_short_circuits():
    settings = mock.Mock()
    settings.ENABLE_INTENT_ROUTING = False
    with mock.patch.object(intent, "get_settings", return_value=settings):
        r = _run(intent.classify_intent("anything"))
    assert r["intent"] == "fact_retrieval"
