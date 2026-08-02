"""Provider tests for the embedding service (SiliconFlow + DashScope).

Covers both providers behind the shared interface: retry/backoff semantics,
request payload shapes (DashScope native contents + pinned MRL dimension),
response parsing (index-ordered), dimension-drift rejection, and the
model-namespaced SQLite cache.

Run: cd backend && ../.venv/Scripts/python.exe tests/test_embedding.py
Exit code 0 = all passed, 1 = any failed.

pytest: the async cases are driven by module-level asyncio.run calls at
import time; the case coroutines are deliberately NOT named test_* (the
provider-parameterized ones would be collected as broken fixtures). One
synchronous ``test_all_embedding_checks_passed`` mirrors the results into
the pytest report without re-running the (real-sleep) retry cases.
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile

import httpx

# Allow running this file directly: ../.venv/Scripts/python.exe tests/test_embedding.py
sys.path.insert(0, ".")

# Capture the real httpx.AsyncClient BEFORE any test patches the module
# attribute. Without this, a second `class C(httpx.AsyncClient)` would
# inherit from the FIRST test's patched subclass — and capture the first
# test's transport in its closure, defeating the patch.
_ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient

import app.services.embedding as embedding_module  # noqa: E402
from app.services.embedding import (  # noqa: E402
    EmbeddingService,
    EmbeddingServiceError,
    MAX_ATTEMPTS,
    RETRY_DELAYS_SECONDS,
    _looks_like_json_embedding,
)


PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_failures: list[str] = []

VEC = [0.1, 0.2, 0.3, 0.4]
VEC2 = [0.5, 0.6, 0.7, 0.8]
PROVIDERS = ("siliconflow", "dashscope")


def check(name: str, cond: bool, detail: str = ""):
    status = PASS if cond else FAIL
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


# ---------- response builders (per-provider wire shapes) ----------------------

def sf_ok(vectors: list[list[float]]) -> bytes:
    """SiliconFlow / OpenAI-compatible response body."""
    return json.dumps({"data": [{"embedding": v} for v in vectors]}).encode()


def ds_ok(vectors: list[list[float]], indices: list[int] | None = None) -> bytes:
    """DashScope native multimodal response body (with usage block)."""
    idxs = indices if indices is not None else list(range(len(vectors)))
    return json.dumps({
        "output": {
            "embeddings": [
                {"embedding": v, "index": i} for v, i in zip(vectors, idxs)
            ]
        },
        "usage": {"image_tokens": 0, "input_tokens": 3, "total_tokens": 3},
    }).encode()


JSON_HEADERS = {"content-type": "application/json"}


# ---------- transport stubs ---------------------------------------------------

class ScriptedTransport(httpx.AsyncBaseTransport):
    """Returns queued (status, body, headers) tuples; records every request."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0
        self.requests: list[dict] = []

    async def handle_async_request(self, request):
        assert self._responses, "transport ran out of scripted responses"
        self.calls += 1
        body = json.loads(request.content.decode("utf-8")) if request.content else None
        self.requests.append({
            "url": str(request.url),
            "auth": request.headers.get("authorization"),
            "body": body,
        })
        status, resp_body, headers = self._responses.pop(0)
        return httpx.Response(
            status_code=status, content=resp_body, headers=headers, request=request
        )


class ScriptedRaiseTransport(httpx.AsyncBaseTransport):
    def __init__(self, exc):
        self._exc = exc
        self.calls = 0

    async def handle_async_request(self, request):
        self.calls += 1
        raise self._exc


class FakeSettings:
    """Deterministic settings stand-in — no config/.env reads, no secrets."""

    def __init__(self, provider: str, sqlite_path: str = ":memory:"):
        self.EMBEDDING_PROVIDER = provider
        self.SILICON_FLOW_BASE_URL = "https://sf.example.test/v1"
        self.SILICON_FLOW_API_KEY = "test-sf-key"
        self.BAILIAN_API_KEY = "test-bailian-key"
        self.DASHSCOPE_EMBEDDING_URL = "https://ds.example.test/native-embed"
        self.DASHSCOPE_EMBEDDING_MODEL = "test-vl-model"
        self.EMBEDDING_MODEL = "test-sf-model"
        self.EMBEDDING_DIM = 4
        self.SQLITE_PATH = sqlite_path


def make_service(provider: str = "siliconflow", sqlite_path: str = ":memory:"):
    """Instantiate via the REAL __init__ with a patched get_settings.

    Exercising the real constructor keeps the provider branch under test —
    a regression in __init__ (wrong key/url/model wiring) fails here.
    """
    fake = FakeSettings(provider, sqlite_path)
    original = embedding_module.get_settings
    embedding_module.get_settings = lambda: fake
    try:
        return EmbeddingService()
    finally:
        embedding_module.get_settings = original


def patch_client(monkey_target_module, transport):
    """Replace httpx.AsyncClient so all instantiations use the scripted transport."""

    class _Client(_ORIGINAL_ASYNC_CLIENT):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(transport=transport, timeout=5.0)

    monkey_target_module.AsyncClient = _Client


def _ok_body(provider: str, vectors: list[list[float]]) -> bytes:
    return ds_ok(vectors) if provider == "dashscope" else sf_ok(vectors)


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


# ---------- retry / backoff (both providers) ----------------------------------

async def _case_retry_then_succeed_on_5xx(provider: str):
    t = ScriptedTransport([
        (503, b"upstream busy", JSON_HEADERS),
        (503, b"upstream busy", JSON_HEADERS),
        (200, _ok_body(provider, [VEC]), JSON_HEADERS),
    ])
    patch_client(httpx, t)
    svc = make_service(provider)
    emb = await svc.embed_single("hello", use_cache=False)
    check(
        f"[{provider}] 5xx × 2 then 200 → returns vector after 3 calls",
        emb == VEC and t.calls == 3,
        f"got {emb!r}, {t.calls} calls",
    )


async def _case_4xx_fast_fail(provider: str):
    t = ScriptedTransport([
        (401, b'{"error":"invalid api key"}', JSON_HEADERS),
    ])
    patch_client(httpx, t)
    svc = make_service(provider)
    try:
        await svc.embed_single("hello", use_cache=False)
        check(f"[{provider}] 401 raises EmbeddingServiceError", False, "no exception")
    except EmbeddingServiceError as e:
        check(
            f"[{provider}] 401 raises on the FIRST attempt (no retries)",
            "401" in str(e) and t.calls == 1,
            f"{t.calls} calls, msg={e}",
        )


async def _case_5xx_exhausts_retries(provider: str):
    t = ScriptedTransport([(503, b"busy", {}) for _ in range(MAX_ATTEMPTS)])
    patch_client(httpx, t)
    svc = make_service(provider)
    try:
        await svc.embed_single("hello", use_cache=False)
        check(f"[{provider}] persistent 5xx raises", False, "no exception")
    except EmbeddingServiceError as e:
        check(
            f"[{provider}] persistent 5xx gives up after {MAX_ATTEMPTS} attempts",
            t.calls == MAX_ATTEMPTS and "503" in str(e),
            f"{t.calls} calls, msg={e}",
        )


async def _case_transport_error_exhausts_retries(provider: str):
    t = ScriptedRaiseTransport(httpx.RemoteProtocolError("conn reset"))
    patch_client(httpx, t)
    svc = make_service(provider)
    try:
        await svc.embed_single("hello", use_cache=False)
        check(f"[{provider}] persistent transport error raises", False, "no exception")
    except EmbeddingServiceError as e:
        check(
            f"[{provider}] transport error gives up after {MAX_ATTEMPTS} attempts",
            t.calls == MAX_ATTEMPTS and "unreachable" in str(e).lower(),
            f"{t.calls} calls, msg={e}",
        )


print("\nRetry / backoff")
check(
    "Retry schedule matches spec (1, 2, 4, 8, 16s, 5 attempts)",
    RETRY_DELAYS_SECONDS == [1, 2, 4, 8, 16] and MAX_ATTEMPTS == 5,
)

# The exhaustion cases would otherwise pay the REAL backoff sleeps
# (1+2+4+8 s × cases × providers ≈ 60 s). Timing is not under test — the
# schedule constants are asserted above — so make sleep instant for this
# section, then restore. embedding.py shares this asyncio module object.
_real_sleep = asyncio.sleep


async def _instant_sleep(_seconds):
    return None


asyncio.sleep = _instant_sleep
try:
    for _provider in PROVIDERS:
        asyncio.run(_case_retry_then_succeed_on_5xx(_provider))
        asyncio.run(_case_4xx_fast_fail(_provider))
        asyncio.run(_case_5xx_exhausts_retries(_provider))
        asyncio.run(_case_transport_error_exhausts_retries(_provider))
finally:
    asyncio.sleep = _real_sleep


# ---------- payload shapes & auth wiring --------------------------------------

async def _case_payload_and_auth(provider: str):
    t = ScriptedTransport([(200, _ok_body(provider, [VEC]), JSON_HEADERS)])
    patch_client(httpx, t)
    svc = make_service(provider)
    await svc.embed_single("hello", use_cache=False)
    req = t.requests[0]

    if provider == "dashscope":
        body = req["body"]
        check(
            "[dashscope] POSTs the native endpoint with BAILIAN key",
            req["url"] == "https://ds.example.test/native-embed"
            and req["auth"] == "Bearer test-bailian-key",
            f"url={req['url']}, auth={req['auth']}",
        )
        check(
            "[dashscope] payload = contents items + pinned MRL dimension",
            body["model"] == "test-vl-model"
            and body["input"] == {"contents": [{"text": "hello"}]}
            and body["parameters"] == {"dimension": 4},
            f"body={body}",
        )
    else:
        body = req["body"]
        check(
            "[siliconflow] POSTs {base}/embeddings with SILICON_FLOW key",
            req["url"] == "https://sf.example.test/v1/embeddings"
            and req["auth"] == "Bearer test-sf-key",
            f"url={req['url']}, auth={req['auth']}",
        )
        check(
            "[siliconflow] single text goes out as a BARE string (unchanged wire format)",
            body["model"] == "test-sf-model"
            and body["input"] == "hello"
            and body["encoding_format"] == "float",
            f"body={body}",
        )


async def _case_batch_payload(provider: str):
    t = ScriptedTransport([(200, _ok_body(provider, [VEC, VEC2]), JSON_HEADERS)])
    patch_client(httpx, t)
    svc = make_service(provider)
    out = await svc.embed_batch(["alpha", "beta"], use_cache=False)
    body = t.requests[0]["body"]

    if provider == "dashscope":
        expected_input = {"contents": [{"text": "alpha"}, {"text": "beta"}]}
    else:
        expected_input = ["alpha", "beta"]  # batch → list on the wire
    check(
        f"[{provider}] batch payload carries one item per text, results in order",
        body["input"] == expected_input and out == [VEC, VEC2],
        f"input={body['input']}, out={out}",
    )


print("\nPayload shapes & auth")
for _provider in PROVIDERS:
    asyncio.run(_case_payload_and_auth(_provider))
    asyncio.run(_case_batch_payload(_provider))


# ---------- dashscope response parsing ----------------------------------------

async def _case_ds_index_ordering():
    """Embeddings arriving out of input order must be re-sorted by index."""
    t = ScriptedTransport([(200, ds_ok([VEC2, VEC], indices=[1, 0]), JSON_HEADERS)])
    patch_client(httpx, t)
    svc = make_service("dashscope")
    out = await svc.embed_batch(["alpha", "beta"], use_cache=False)
    check(
        "[dashscope] out-of-order `index` values → input order restored",
        out == [VEC, VEC2],
        f"got {out}",
    )


async def _case_ds_dim_mismatch_rejected():
    """A 3-dim vector where 4 was pinned must raise (MRL drift guard)."""
    short = json.dumps(
        {"output": {"embeddings": [{"embedding": [0.1, 0.2, 0.3], "index": 0}]}}
    ).encode()
    t = ScriptedTransport([(200, short, JSON_HEADERS)])
    patch_client(httpx, t)
    svc = make_service("dashscope")
    try:
        await svc.embed_single("hello", use_cache=False)
        check("[dashscope] dim mismatch raises EmbeddingServiceError", False, "no exception")
    except EmbeddingServiceError as e:
        check(
            "[dashscope] dim mismatch (3 vs pinned 4) raises with MRL hint",
            "dim" in str(e) and "MRL" in str(e),
            f"msg={e}",
        )


async def _case_ds_count_mismatch_rejected():
    """More vectors than inputs must raise, not silently misalign."""
    t = ScriptedTransport([(200, ds_ok([VEC, VEC2]), JSON_HEADERS)])
    patch_client(httpx, t)
    svc = make_service("dashscope")
    try:
        await svc.embed_single("hello", use_cache=False)
        check("[dashscope] count mismatch raises", False, "no exception")
    except EmbeddingServiceError as e:
        check(
            "[dashscope] 2 vectors for 1 input raises with counts",
            "2 embeddings" in str(e) and "1 inputs" in str(e),
            f"msg={e}",
        )


print("\nDashScope response parsing")
asyncio.run(_case_ds_index_ordering())
asyncio.run(_case_ds_dim_mismatch_rejected())
asyncio.run(_case_ds_count_mismatch_rejected())


# ---------- cache (tempfile SQLite — :memory: is per-connection) --------------

async def _case_cache_roundtrip_and_model_namespace():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute(
        """CREATE TABLE embedding_cache (
               text_hash TEXT PRIMARY KEY,
               text TEXT NOT NULL,
               embedding BLOB NOT NULL,
               model TEXT NOT NULL,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.commit()
    conn.close()
    try:
        t1 = ScriptedTransport([
            (200, sf_ok([VEC]), JSON_HEADERS),
            (200, sf_ok([VEC]), JSON_HEADERS),  # must NOT be consumed
        ])
        patch_client(httpx, t1)
        svc = make_service("siliconflow", sqlite_path=tmp.name)
        first = await svc.embed_single("cache me", use_cache=True)
        second = await svc.embed_single("cache me", use_cache=True)
        check(
            "second identical call is served from cache (exactly 1 HTTP call)",
            first == VEC and second == VEC and t1.calls == 1,
            f"{t1.calls} calls",
        )

        # Different provider → different model column → cache miss, new call.
        t2 = ScriptedTransport([(200, ds_ok([VEC2]), JSON_HEADERS)])
        patch_client(httpx, t2)
        svc2 = make_service("dashscope", sqlite_path=tmp.name)
        third = await svc2.embed_single("cache me", use_cache=True)
        check(
            "provider/model namespacing: dashscope does not read siliconflow rows",
            third == VEC2 and t2.calls == 1,
            f"{t2.calls} calls, got {third}",
        )
    finally:
        os.unlink(tmp.name)


print("\nCache")
asyncio.run(_case_cache_roundtrip_and_model_namespace())


def test_all_embedding_checks_passed():
    """pytest mirror: every case above already ran at import time.

    Re-running them here would pay the real retry backoff sleeps again
    (~60s) for no extra signal — this just surfaces the recorded verdicts.
    """
    assert not _failures, (
        f"{len(_failures)} embedding checks failed: {', '.join(_failures)}"
    )


print()
if _failures:
    print(f"\033[91m{len(_failures)} FAILED:\033[0m " + ", ".join(_failures))
    sys.exit(1)
print(f"\033[92mAll checks passed.\033[0m")
