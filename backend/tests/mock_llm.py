"""httpx.MockTransport-backed LLMService factory for offline tests.

Mocking sits at the HTTP transport layer (not the service object layer), so
LLMService's own parsing — SSE frame handling, content=null normalization,
retry-once logic, truncation markers, enable_thinking 400-fallback — is
exercised exactly as in production. Zero network, zero new dependencies
(httpx.MockTransport ships with httpx 0.27 already pinned).

Frame shapes mirror what DashScope's OpenAI-compatible endpoint actually
emits (verified against app/services/llm.py::_stream_completions branches):
- reasoning_content deltas BEFORE answer content when thinking is enabled
- role-only / usage-only frames with EMPTY choices lists or content:null
- a terminal ``data: [DONE]`` line
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from app.services.llm import LLMService


@dataclass
class MockCallRecorder:
    """Records every request the mocked client sent."""
    requests: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def call_count(self) -> int:
        return len(self.requests)

    def last_payload(self) -> Dict[str, Any]:
        return self.requests[-1]["json"] if self.requests else {}


def _nonstream_body(content: Optional[str], finish_reason: str) -> Dict[str, Any]:
    return {
        "choices": [{
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }]
    }


def _sse_frames_to_text(frames: List[Dict[str, Any]]) -> str:
    lines = []
    for frame in frames:
        lines.append(f"data: {json.dumps(frame)}")
    lines.append("data: [DONE]")
    return "\n\n".join(lines) + "\n\n"


def make_mock_llm_service(
    *,
    content: str = "测试回答 [1]",
    thinking: Optional[str] = None,
    finish_reason: str = "stop",
    status_sequence: Optional[List[int]] = None,
    sse_frames: Optional[List[Dict[str, Any]]] = None,
    base_url: str = "https://mock.test/compatible-mode/v1",
) -> tuple[LLMService, MockCallRecorder]:
    """Build an LLMService wired to an in-process httpx.MockTransport.

    Args mirror the three response families the transport tests exercise:
      - content/thinking/finish_reason: canned non-stream JSON body; when
        ``thinking`` is set, a matching SSE stream is synthesized too.
      - status_sequence: non-empty list makes the transport return those
        status codes first (one per request), then succeed. Use to drive
        retry/fallback paths.
      - sse_frames: fully custom stream frames (advanced); overrides the
        synthesized ones.
    """
    recorder = MockCallRecorder()
    statuses = list(status_sequence or [])
    call_index = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content.decode("utf-8"))
        recorder.requests.append({
            "url": str(request.url),
            "json": payload,
            "headers": dict(request.headers),
        })

        idx = call_index["n"]
        call_index["n"] += 1
        if idx < len(statuses):
            return httpx.Response(
                statuses[idx],
                json={"error": {"message": f"forced status {statuses[idx]}"}},
                request=request,
            )

        if payload.get("stream"):
            frames = sse_frames
            if frames is None:
                frames = []
                if thinking is not None:
                    frames.append({"choices": [{"delta": {
                        "reasoning_content": thinking}}]})
                    frames.append({"choices": [{"delta": {
                        "reasoning_content": None}}]})
                frames.append({"choices": [{"delta": {
                    "role": "assistant", "content": None}}]})
                for ch in content:
                    frames.append({"choices": [{"delta": {"content": ch}}]})
                frames.append({"choices": []})  # usage-only trailing frame
            return httpx.Response(
                200,
                text=_sse_frames_to_text(frames),
                headers={"content-type": "text/event-stream"},
                request=request,
            )
        return httpx.Response(
            200,
            json=_nonstream_body(content, finish_reason),
            request=request,
        )

    service = LLMService()
    # Override provider coordinates so no real host is ever reachable even
    # if a test accidentally bypasses the transport.
    service.base_url = base_url
    service.api_key = "test-key-not-real"
    service._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return service, recorder
