"""Transport-layer tests for LLMService over httpx.MockTransport (T1-3).

These exercise the REAL parsing/retry/normalization code paths in
app/services/llm.py — no service-object mocking, no network.
"""
import asyncio
import json

import pytest

# Flat-module import: the site-packages tree shadows the local ``tests``
# directory as a package, so ``from tests.helpers...`` is not reliable here.
from mock_llm import make_mock_llm_service


# ---------- non-streaming ----------

class TestNonStream:
    @pytest.mark.asyncio
    async def test_happy_path_returns_content(self):
        llm, rec = make_mock_llm_service(content="答案正文")
        out = await llm.chat_complete([{"role": "user", "content": "q"}])
        assert out == "答案正文"
        assert rec.call_count == 1
        assert "enable_thinking" not in rec.last_payload()

    @pytest.mark.asyncio
    async def test_content_null_normalizes_to_empty(self):
        from unittest import mock

        llm, _ = make_mock_llm_service(content=None)
        with mock.patch("asyncio.sleep", mock.AsyncMock()):
            out = await llm.chat_complete(
                [{"role": "user", "content": "q"}], enable_thinking=False,
            )
        # content=None -> "" (never persisted as NULL); enable_thinking=False
        # on a mocked 200 is passed through untouched, so no retry happens.
        assert out == ""

    @pytest.mark.asyncio
    async def test_length_finish_appends_truncation_marker(self):
        llm, _ = make_mock_llm_service(
            content="被截断的答案", finish_reason="length",
        )
        out = await llm.chat_complete(
            [{"role": "user", "content": "q"}],
            truncation_marker="…（已截断，输出达到上限）",
        )
        assert out.endswith("…（已截断，输出达到上限）")
        assert out.startswith("被截断的答案")

    @pytest.mark.asyncio
    async def test_length_finish_without_marker_is_untouched(self):
        llm, _ = make_mock_llm_service(content="截断但无标记", finish_reason="length")
        out = await llm.chat_complete([{"role": "user", "content": "q"}])
        assert out == "截断但无标记"

    @pytest.mark.asyncio
    async def test_api_key_missing_raises_fast(self):
        llm, rec = make_mock_llm_service()
        llm.api_key = ""
        with pytest.raises(ValueError, match="No API key"):
            await llm.chat_complete([{"role": "user", "content": "q"}])
        assert rec.call_count == 0


# ---------- streaming ----------

class TestStream:
    @pytest.mark.asyncio
    async def test_stream_reassembles_content_in_order(self):
        llm, rec = make_mock_llm_service(content="流式回答")
        parts = [(kind, text) async for kind, text in llm.chat_complete_stream(
            [{"role": "user", "content": "q"}]
        )]
        kinds = {kind for kind, _ in parts}
        assert kinds == {"content"}
        assert "".join(text for _, text in parts) == "流式回答"
        assert rec.last_payload()["stream"] is True

    @pytest.mark.asyncio
    async def test_stream_thinking_frames_precede_content(self):
        llm, _ = make_mock_llm_service(content="答", thinking="思考过程")
        parts = [item async for item in llm.chat_complete_stream(
            [{"role": "user", "content": "q"}], enable_thinking=True,
        )]
        assert ("thinking", "思考过程") in parts
        thinking_idx = parts.index(("thinking", "思考过程"))
        content_idx = next(i for i, (k, _) in enumerate(parts) if k == "content")
        assert thinking_idx < content_idx

    @pytest.mark.asyncio
    async def test_stream_skips_null_and_empty_choices_frames(self):
        frames = [
            {"choices": [{"delta": {"role": "assistant", "content": None}}]},
            {"choices": []},  # usage-only frame: must not IndexError
            {"choices": [{"delta": {"content": "好"}}]},
        ]
        llm, _ = make_mock_llm_service(sse_frames=frames)
        parts = [item async for item in llm.chat_complete_stream(
            [{"role": "user", "content": "q"}]
        )]
        assert parts == [("content", "好")]

    @pytest.mark.asyncio
    async def test_custom_sse_frames_pass_through_verbatim(self):
        frames = [
            {"choices": [{"delta": {"content": "第一段"}}]},
            {"choices": [{"delta": {"content": "第二段"}}]},
        ]
        llm, _ = make_mock_llm_service(sse_frames=frames)
        parts = [t async for k, t in llm.chat_complete_stream([])]
        assert parts == ["第一段", "第二段"]


# ---------- retries & fallbacks ----------

class TestRetries:
    @pytest.mark.asyncio
    async def test_500_then_success_recovers(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
        llm, rec = make_mock_llm_service(status_sequence=[500])
        out = await llm.chat_complete([{"role": "user", "content": "q"}])
        assert out == "测试回答 [1]"
        assert rec.call_count == 2

    @pytest.mark.asyncio
    async def test_double_500_recovers_on_third_attempt(self, monkeypatch):
        # chat_complete now uses the shared retry policy (max_attempts=3):
        # two transient 500s are absorbed and the third try succeeds.
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
        llm, rec = make_mock_llm_service(status_sequence=[500, 500])
        out = await llm.chat_complete([{"role": "user", "content": "q"}])
        assert out == "测试回答 [1]"
        assert rec.call_count == 3

    @pytest.mark.asyncio
    async def test_triple_500_raises_with_cause(self, monkeypatch):
        # Exhausting all three attempts raises the LAST failure (an
        # HTTPStatusError from run_with_retry, so the provider's status and
        # body stay visible in logs).
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
        llm, rec = make_mock_llm_service(status_sequence=[500, 500, 500])
        with pytest.raises(__import__("httpx").HTTPStatusError) as exc_info:
            await llm.chat_complete([{"role": "user", "content": "q"}])
        assert exc_info.value.response.status_code == 500
        assert rec.call_count == 3

    @pytest.mark.asyncio
    async def test_400_with_enable_thinking_drops_param_and_retries_once(self):
        # The transport rejects any request carrying enable_thinking with a
        # 400 (mirroring non-thinking model tiers); LLMService must drop the
        # param and retry ONCE. The rejected first call never reaches the
        # mock handler, so only the successful retry lands in the recorder.
        llm, rec = make_mock_llm_service()

        original_send = llm._client._transport.handle_async_request

        async def reject_enable_thinking(request):
            payload = json.loads(request.content.decode())
            if "enable_thinking" in payload:
                return __import__("httpx").Response(
                    400, json={"error": "unsupported param"}, request=request,
                )
            return await original_send(request)

        llm._client._transport.handle_async_request = reject_enable_thinking

        out = await llm.chat_complete(
            [{"role": "user", "content": "q"}], enable_thinking=True,
        )
        assert out == "测试回答 [1]"
        assert rec.call_count == 1  # only the param-dropped retry arrives
        assert "enable_thinking" not in rec.last_payload()

    @pytest.mark.asyncio
    async def test_404_never_retries(self):
        llm, rec = make_mock_llm_service(status_sequence=[404])
        with pytest.raises(Exception):
            await llm.chat_complete([{"role": "user", "content": "q"}])
        assert rec.call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_retries_once(self, monkeypatch):
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
        llm, rec = make_mock_llm_service()

        import httpx as _httpx
        original = llm._client._transport.handle_async_request

        calls = {"n": 0}

        async def flaky(request):
            calls["n"] += 1
            if calls["n"] == 1:
                # ReadTimeout is in _RETRYABLE_EXCEPTIONS (ConnectTimeout is not).
                raise _httpx.ReadTimeout("timed out", request=request)
            return await original(request)

        llm._client._transport.handle_async_request = flaky
        out = await llm.chat_complete([{"role": "user", "content": "q"}])
        assert out == "测试回答 [1]"
        # The timed-out first attempt never reaches the mock handler, so
        # assert on the transport-level counter instead of the recorder.
        assert calls["n"] == 2
        assert rec.call_count == 1


    @pytest.mark.asyncio
    async def test_stream_transport_error_before_first_frame_retries(self, monkeypatch):
        # A transport error BEFORE any delta reached the caller is retried
        # transparently (nothing was streamed, so replay cannot duplicate).
        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
        llm, rec = make_mock_llm_service()

        import httpx as _httpx
        original = llm._client._transport.handle_async_request

        calls = {"n": 0}

        async def flaky(request):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _httpx.RemoteProtocolError("peer reset", request=request)
            return await original(request)

        llm._client._transport.handle_async_request = flaky
        parts = [item async for item in llm.chat_complete_stream(
            [{"role": "user", "content": "q"}]
        )]
        kinds = {kind for kind, _ in parts}
        assert kinds == {"content"}          # no error frame leaked
        assert "".join(t for k, t in parts if k == "content") == "测试回答 [1]"
        # The reset first attempt never reaches the mock handler, so count
        # at the transport level (same convention as test_timeout_retries_once).
        assert calls["n"] == 2
        assert rec.call_count == 1

    @pytest.mark.asyncio
    async def test_stream_mid_stream_error_surfaces_as_error_frame(self):
        # After the first content delta has been yielded, a failure must NOT
        # be retried (replay would duplicate text) — it surfaces as the
        # terminal ("error", ...) frame instead.
        llm, _ = make_mock_llm_service()

        import httpx as _httpx
        original = llm._client._transport.handle_async_request

        async def die_midstream(request):
            # Synthesize an SSE body that yields one good frame then dies.
            async def gen():
                yield b'data: {"choices":[{"delta":{"content":"\xe5\x89\x8d"}}]}\n\n'
                raise _httpx.RemoteProtocolError("connection lost mid-stream")
            return _httpx.Response(
                200, content=gen(),
                headers={"content-type": "text/event-stream"}, request=request,
            )

        llm._client._transport.handle_async_request = die_midstream
        parts = [item async for item in llm.chat_complete_stream(
            [{"role": "user", "content": "q"}]
        )]
        assert parts[0] == ("content", "前")
        assert parts[-1][0] == "error"
        assert "mid-stream" in parts[-1][1]


async def _noop_sleep(_seconds):
    """Retry backoff stub: no real waiting inside tests."""
    return None
