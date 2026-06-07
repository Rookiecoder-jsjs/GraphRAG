"""Tests for #0 follow-up suggestions.

Standalone runner:
    cd backend
    ../.venv/Scripts/python.exe tests/test_followups.py

Coverage:
  1. ChatRequest.with_followups defaults to True
  2. ChatRequest.with_followups=False is honored
  3. ChatResponse.followups field is present and defaults to []
  4. LLMService.generate_followups method exists with correct signature
  5. generate_followups parses a clean JSON array of 3 questions
  6. generate_followups parses JSON with surrounding prose (extract_json)
  7. generate_followups caps at 3 results and drops blanks
  8. generate_followups returns [] on LLM error (never raises to caller)
  9. generate_followups returns [] when the JSON has fewer than 3 entries
 10. Stream generator emits a 'followups' SSE event when with_followups=True
 11. Stream generator skips followups entirely when with_followups=False
"""
from __future__ import annotations

import asyncio
import inspect
import json
import sys
import unittest.mock as _mock
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# =========================================================================
# Standalone runner
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


# =========================================================================
# 1. Pydantic model
# =========================================================================

def test_chat_request_default_with_followups_is_true():
    """Default ON — the whole point of the feature is to suggest next steps."""
    from app.models.chat import ChatRequest
    req = ChatRequest(message="hi")
    check("ChatRequest: with_followups defaults to True",
          req.with_followups is True)


def test_chat_request_explicit_with_followups_false():
    """User can opt out (e.g. for a streaming benchmark run)."""
    from app.models.chat import ChatRequest
    req = ChatRequest(message="hi", with_followups=False)
    check("ChatRequest: with_followups=False round-trips",
          req.with_followups is False)


def test_chat_response_has_followups_field():
    """Followups default to an empty list so the field is always present
    in the response envelope (no 'undefined' in the frontend)."""
    from app.models.chat import ChatResponse
    resp = ChatResponse(message="x", conversation_id="c1")
    check("ChatResponse: followups field exists", hasattr(resp, "followups"))
    check("ChatResponse: followups defaults to []",
          resp.followups == [])


# =========================================================================
# 2. LLMService.generate_followups
# =========================================================================

def test_llm_service_has_generate_followups_method():
    from app.services.llm import LLMService
    sig = inspect.signature(LLMService.generate_followups)
    params = sig.parameters
    check("LLMService.generate_followups exists",
          hasattr(LLMService, "generate_followups"))
    check("LLMService.generate_followups is async",
          inspect.iscoroutinefunction(LLMService.generate_followups))
    check("LLMService.generate_followups has 'query' param",
          "query" in params)
    check("LLMService.generate_followups has 'answer' param",
          "answer" in params)
    check("LLMService.generate_followups has 'n' param with default 3",
          "n" in params and params["n"].default == 3)


def test_generate_followups_parses_clean_json():
    """The LLM returns a clean JSON array — parse it as-is."""
    from app.services.llm import LLMService
    svc = LLMService()
    raw = json.dumps([
        "What is the difference between QLoRA and LoRA?",
        "Which papers introduced retrieval-augmented generation?",
        "How does the reranker work in this system?",
    ])
    with _mock.patch.object(svc, "chat_complete", _mock.AsyncMock(return_value=raw)):
        out = asyncio.run(svc.generate_followups(
            query="Explain QLoRA", answer="QLoRA is …"))
    check("generate_followups: returns 3 strings from clean JSON",
          len(out) == 3
          and out[0].startswith("What is the difference"))
    check("generate_followups: each entry is a non-empty string",
          all(isinstance(s, str) and s.strip() for s in out))


def test_generate_followups_parses_json_with_surrounding_prose():
    """LLMs often wrap JSON in ```json ... ``` fences or preambles — we
    must extract the array even when the response isn't pure JSON."""
    from app.services.llm import LLMService
    svc = LLMService()
    raw = (
        "Here are 3 follow-up questions:\n"
        "```json\n"
        + json.dumps([
            "How does chunking work?",
            "What is the embedding model?",
            "Why use Neo4j?",
        ])
        + "\n```\n"
    )
    with _mock.patch.object(svc, "chat_complete", _mock.AsyncMock(return_value=raw)):
        out = asyncio.run(svc.generate_followups(query="q", answer="a"))
    check("generate_followups: extracts array from fenced prose",
          len(out) == 3 and out[0] == "How does chunking work?")


def test_generate_followups_caps_at_n_and_drops_blanks():
    """If the LLM returns 5 (n=3) or has empty strings, we cap and clean."""
    from app.services.llm import LLMService
    svc = LLMService()
    raw = json.dumps([
        "Good question 1",
        "",
        "  ",
        "Good question 2",
        "Good question 3",
    ])
    with _mock.patch.object(svc, "chat_complete", _mock.AsyncMock(return_value=raw)):
        out = asyncio.run(svc.generate_followups(query="q", answer="a", n=3))
    check("generate_followups: caps at n=3", len(out) == 3)
    check("generate_followups: drops blank entries",
          out == ["Good question 1", "Good question 2", "Good question 3"])


def test_generate_followups_returns_empty_on_llm_error():
    """A failure here must NEVER propagate — followups are a UX bonus.
    The chat response should still be valid (just with no chips)."""
    from app.services.llm import LLMService
    svc = LLMService()
    with _mock.patch.object(
        svc, "chat_complete",
        _mock.AsyncMock(side_effect=RuntimeError("rate limit")),
    ):
        out = asyncio.run(svc.generate_followups(query="q", answer="a"))
    check("generate_followups: returns [] on LLM error", out == [])


def test_generate_followups_returns_empty_on_garbage():
    """If the LLM returns text with no JSON array at all, we don't crash."""
    from app.services.llm import LLMService
    svc = LLMService()
    with _mock.patch.object(
        svc, "chat_complete",
        _mock.AsyncMock(return_value="I cannot generate followups right now."),
    ):
        out = asyncio.run(svc.generate_followups(query="q", answer="a"))
    check("generate_followups: returns [] when no JSON in response",
          out == [])


def test_generate_followups_handles_fewer_than_n():
    """LLM sometimes returns only 2 — return what we got (length 2),
    the client renders what it gets (no padding)."""
    from app.services.llm import LLMService
    svc = LLMService()
    raw = json.dumps(["Q1", "Q2"])
    with _mock.patch.object(svc, "chat_complete", _mock.AsyncMock(return_value=raw)):
        out = asyncio.run(svc.generate_followups(query="q", answer="a", n=3))
    check("generate_followups: returns 2 when LLM gives 2", len(out) == 2)


# =========================================================================
# 3. chat_stream_generator emits a 'followups' SSE event
# =========================================================================

class _FakeStreamLLM:
    """Stub for LLMService used by chat_stream_generator tests."""
    def __init__(self, full_text="answer body", followups=None,
                 followups_raises=False):
        self._text = full_text
        self._followups = followups or []
        self._raises = followups_raises
        self.chat_complete_calls = 0
        self.generate_followups_calls = 0

    async def chat_complete_stream(self, messages):
        for chunk in self._text.split(" "):
            yield chunk + " "

    async def generate_followups(self, query, answer, n=3):
        self.generate_followups_calls += 1
        if self._raises:
            raise RuntimeError("boom")
        return list(self._followups)


def _collect_sse_events(generator):
    """Drain the async generator and return the joined body."""
    out = []
    async def _drain():
        async for chunk in generator:
            out.append(chunk)
    asyncio.run(_drain())
    return "".join(out)


def _make_stub_db():
    """No-op DB context manager that returns a cursor returning empty results.

    aiosqlite's `db.execute()` is sync and returns an `AsyncCursor` that is
    BOTH awaitable (for `await db.execute(INSERT...)` in non-SELECT paths)
    AND an async context manager (for `async with db.execute(SELECT...)`).
    The fake must support both usage patterns from chat.py."""
    class _CursorCtx:
        # Context manager protocol: `async with db.execute(...) as cursor:`
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        # Awaitable protocol: `await db.execute(INSERT/UPDATE/DELETE...)`
        # (the production code uses await for non-SELECT writes)
        def __await__(self):
            async def _coro():
                return self
            return _coro().__await__()
        async def fetchall(self): return []
        async def fetchone(self): return None
        @property
        def rowcount(self): return 0

    class _DB:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def execute(self, *a, **kw): return _CursorCtx()
        async def commit(self): return None
        @property
        def rowcount(self): return 0

    def _stub():
        return _DB()
    return _stub


def test_stream_generator_emits_followups_event():
    """When with_followups=True, a 'event: followups' SSE block should
    appear in the stream with the list of questions as JSON data.

    Also covers the disabled case (with_followups=False) in the same
    test. Both cases must run in ONE event loop — calling `asyncio.run`
    twice in this process trips an httpx telemetry shutdown that raises
    "Event loop is closed" on the second loop.
    """
    from app.api import chat as chat_mod
    from app.models.chat import ChatRequest

    fake_llm_on = _FakeStreamLLM(
        full_text="streaming answer",
        followups=["Q1?", "Q2?", "Q3?"],
    )
    fake_llm_off = _FakeStreamLLM(full_text="x", followups=["Q1", "Q2", "Q3"])

    async def _run_both():
        # Each case uses its own get_llm_service; swap inside the loop.
        async def _drain(req, llm):
            async def _stub():
                return llm
            with _mock.patch.object(chat_mod, "get_db", _make_stub_db()), \
                 _mock.patch.object(chat_mod, "get_llm_service", _stub):
                gen = chat_mod.chat_stream_generator(req, user_id=1)
                out = []
                async for chunk in gen:
                    out.append(chunk)
                return "".join(out)

        body_on = await _drain(
            ChatRequest(message="hi", with_followups=True), fake_llm_on,
        )
        body_off = await _drain(
            ChatRequest(message="hi", with_followups=False), fake_llm_off,
        )
        return body_on, body_off

    body_on, body_off = asyncio.run(_run_both())

    # ---- Case 1: with_followups=True ----
    check("stream: emits 'event: followups' when on",
          "event: followups" in body_on)
    check("stream: followups data is valid JSON with 3 items",
          '"followups"' in body_on and '"Q1?"' in body_on and '"Q3?"' in body_on)
    check("stream: still emits 'event: done' when on",
          "event: done" in body_on)
    check("stream: called generate_followups exactly once when on",
          fake_llm_on.generate_followups_calls == 1)
    # ---- Case 2: with_followups=False ----
    check("stream: no 'event: followups' when with_followups=False",
          "event: followups" not in body_off)
    check("stream: generate_followups not called when off",
          fake_llm_off.generate_followups_calls == 0)


# =========================================================================
# Driver
# =========================================================================

ALL_TESTS = [
    test_chat_request_default_with_followups_is_true,
    test_chat_request_explicit_with_followups_false,
    test_chat_response_has_followups_field,
    test_llm_service_has_generate_followups_method,
    test_generate_followups_parses_clean_json,
    test_generate_followups_parses_json_with_surrounding_prose,
    test_generate_followups_caps_at_n_and_drops_blanks,
    test_generate_followups_returns_empty_on_llm_error,
    test_generate_followups_returns_empty_on_garbage,
    test_generate_followups_handles_fewer_than_n,
    test_stream_generator_emits_followups_event,
]


def main() -> int:
    print(f"Running {len(ALL_TESTS)} checks for #0 follow-up suggestions...")
    for fn in ALL_TESTS:
        try:
            fn()
        except Exception as e:
            check(f"{fn.__name__}: no unhandled exceptions", False, repr(e))
    print()
    if _failures:
        print(f"{FAIL} {len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print(f"{PASS} All checks passed ({len(ALL_TESTS)} tests).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
