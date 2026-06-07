"""Smoke tests for the embedding service.

Run: cd backend && ../.venv/Scripts/python.exe tests/test_embedding.py
Exit code 0 = all passed, 1 = any failed. No pytest dependency.
"""
import asyncio
import json
import sys
import httpx

# Allow running this file directly: ../.venv/Scripts/python.exe tests/test_embedding.py
sys.path.insert(0, ".")

# Capture the real httpx.AsyncClient BEFORE any test patches the module
# attribute. Without this, a second `class C(httpx.AsyncClient)` would
# inherit from the FIRST test's patched subclass — and capture the first
# test's transport in its closure, defeating the patch.
_ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient

from app.services.embedding import (
    EmbeddingService,
    EmbeddingServiceError,
    MAX_ATTEMPTS,
    RETRY_DELAYS_SECONDS,
    _looks_like_json_embedding,
)


PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_failures: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    status = PASS if cond else FAIL
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


# ---------- format sniffing ---------------------------------------------------

print("Cache format sniffing")
check(
    "Valid JSON array / number prefixes accepted",
    _looks_like_json_embedding(b"[1.0,2.0]") is True
    and _looks_like_json_embedding(b"[-0.5,0,1]") is True
    and _looks_like_json_embedding(b"0.5,1]") is True   # leading digit OK
    and _looks_like_json_embedding(b"") is False,
)
check(
    "Legacy pickle / 0x80 prefix rejected (self-heals via delete)",
    _looks_like_json_embedding(b"\x80\x02]q\x00.") is False
    and _looks_like_json_embedding(b"\x80garbage") is False,
)


# ---------- transport stubs ---------------------------------------------------

class ScriptedTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    async def handle_async_request(self, request):
        assert self._responses, "transport ran out of scripted responses"
        self.calls += 1
        status, body, headers = self._responses.pop(0)
        return httpx.Response(
            status_code=status, content=body, headers=headers, request=request
        )


class ScriptedRaiseTransport(httpx.AsyncBaseTransport):
    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    async def handle_async_request(self, request):
        self.calls += 1
        raise self._exc


def make_service():
    """Build an EmbeddingService with deterministic test settings (no config read)."""
    svc = EmbeddingService.__new__(EmbeddingService)
    svc.settings = type("S", (), {
        "SILICON_FLOW_BASE_URL": "https://example.test/v1",
        "SILICON_FLOW_API_KEY": "test-key",
        "EMBEDDING_MODEL": "test-model",
        "EMBEDDING_DIM": 4,
        "SQLITE_PATH": ":memory:",
    })()
    svc.base_url = svc.settings.SILICON_FLOW_BASE_URL
    svc.api_key = svc.settings.SILICON_FLOW_API_KEY
    svc.model = svc.settings.EMBEDDING_MODEL
    svc._semaphore = asyncio.Semaphore(5)
    return svc


def patch_client(monkey_target_module, transport):
    """Replace httpx.AsyncClient so all instantiations use the scripted transport."""

    class _Client(_ORIGINAL_ASYNC_CLIENT):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(transport=transport, timeout=5.0)

    monkey_target_module.AsyncClient = _Client


# ---------- async tests --------------------------------------------------------

async def test_retry_then_succeed_on_5xx():
    ok = json.dumps({"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]}).encode()
    t = ScriptedTransport([
        (503, b"upstream busy", {"content-type": "application/json"}),
        (503, b"upstream busy", {"content-type": "application/json"}),
        (200, ok, {"content-type": "application/json"}),
    ])
    patch_client(httpx, t)
    svc = make_service()
    emb = await svc.embed_single("hello", use_cache=False)
    check(
        "5xx × 2 then 200 → returns vector after 3 calls",
        emb == [0.1, 0.2, 0.3, 0.4] and t.calls == 3,
        f"got {emb!r}, {t.calls} calls",
    )


async def test_4xx_fast_fail():
    t = ScriptedTransport([
        (401, b'{"error":"invalid api key"}', {"content-type": "application/json"}),
    ])
    patch_client(httpx, t)
    svc = make_service()
    try:
        await svc.embed_single("hello", use_cache=False)
        check("401 raises EmbeddingServiceError", False, "no exception raised")
    except EmbeddingServiceError as e:
        check(
            "401 raises EmbeddingServiceError on the FIRST attempt (no retries)",
            "401" in str(e) and t.calls == 1,
            f"{t.calls} calls, msg={e}",
        )


async def test_5xx_exhausts_retries():
    t = ScriptedTransport([(503, b"busy", {}) for _ in range(MAX_ATTEMPTS)])
    patch_client(httpx, t)
    svc = make_service()
    try:
        await svc.embed_single("hello", use_cache=False)
        check("Persistent 5xx raises EmbeddingServiceError", False, "no exception")
    except EmbeddingServiceError as e:
        check(
            f"Persistent 5xx gives up after {MAX_ATTEMPTS} attempts",
            t.calls == MAX_ATTEMPTS and "503" in str(e),
            f"{t.calls} calls, msg={e}",
        )


async def test_transport_error_exhausts_retries():
    t = ScriptedRaiseTransport(httpx.RemoteProtocolError("conn reset"))
    patch_client(httpx, t)
    svc = make_service()
    try:
        await svc.embed_single("hello", use_cache=False)
        check("Persistent transport error raises", False, "no exception")
    except EmbeddingServiceError as e:
        check(
            f"Persistent transport error gives up after {MAX_ATTEMPTS} attempts",
            t.calls == MAX_ATTEMPTS and "unreachable" in str(e).lower(),
            f"{t.calls} calls, msg={e}",
        )


print("\nRetry / backoff")
check(
    "Retry schedule matches spec (1, 2, 4, 8, 16s, 5 attempts)",
    RETRY_DELAYS_SECONDS == [1, 2, 4, 8, 16] and MAX_ATTEMPTS == 5,
)

asyncio.run(test_retry_then_succeed_on_5xx())
asyncio.run(test_4xx_fast_fail())
asyncio.run(test_5xx_exhausts_retries())
asyncio.run(test_transport_error_exhausts_retries())


print()
if _failures:
    print(f"\033[91m{len(_failures)} FAILED:\033[0m " + ", ".join(_failures))
    sys.exit(1)
print(f"\033[92mAll checks passed.\033[0m")
