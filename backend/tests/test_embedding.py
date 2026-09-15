"""Smoke tests for the embedding service.

Run: cd backend && ../.venv/Scripts/python.exe tests/test_embedding.py
Exit code 0 = all passed, 1 = any failed. No pytest dependency.
"""
import asyncio
import hashlib
import json
import os
import sys
import tempfile
from unittest import mock

import aiosqlite
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
    _looks_like_json_embedding,
)
from app.services.key_pool import KeyPool


PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
_failures: list[str] = []


def check(name: str, cond: bool, detail: str = ""):
    status = PASS if cond else FAIL
    print(f"  [{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(name + (f" — {detail}" if detail else ""))


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


class RecordingTransport(ScriptedTransport):
    """ScriptedTransport that also records each request's JSON body and
    Authorization header (for per-key failover assertions)."""

    def __init__(self, responses):
        super().__init__(responses)
        self.requests: list = []
        self.auth_headers: list = []

    async def handle_async_request(self, request):
        self.requests.append(json.loads(request.content.decode("utf-8")))
        self.auth_headers.append(request.headers.get("authorization", ""))
        return await super().handle_async_request(request)


def make_service(batch_size: int = 32, batch_delay: float = 0.0,
                 db_path: str = ":memory:", pool: KeyPool | None = None):
    """Build an EmbeddingService with deterministic test settings (no config read).

    ``__new__`` skips ``__init__``, so every attribute the service lazily
    touches must be set here explicitly.
    """
    svc = EmbeddingService.__new__(EmbeddingService)
    svc.settings = type("S", (), {
        "SILICON_FLOW_BASE_URL": "https://example.test/v1",
        "SILICON_FLOW_API_KEY": "test-key",
        "EMBEDDING_MODEL": "test-model",
        "EMBEDDING_DIM": 4,
        "SQLITE_PATH": db_path,
        "EMBED_BATCH_SIZE": batch_size,
        "EMBED_BATCH_DELAY_SECONDS": batch_delay,
    })()
    svc.base_url = svc.settings.SILICON_FLOW_BASE_URL
    svc.model = svc.settings.EMBEDDING_MODEL
    svc.pool = pool or KeyPool(["test-key-0000"], per_key_concurrency=5)
    svc._client = None  # lazy shared client, created on first _get_client()
    svc._db = None      # lazy shared cache connection
    svc._db_lock = asyncio.Lock()
    return svc


def patch_client(monkey_target_module, transport):
    """Replace httpx.AsyncClient so all instantiations use the scripted transport."""

    class _Client(_ORIGINAL_ASYNC_CLIENT):
        def __init__(self, *args, **kwargs):
            kwargs.pop("transport", None)
            super().__init__(transport=transport, timeout=5.0)

    monkey_target_module.AsyncClient = _Client


# ---------- async tests --------------------------------------------------------

async def _async_retry_then_succeed_on_5xx():
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


async def _async_4xx_fast_fail():
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


async def _async_5xx_exhausts_retries():
    t = ScriptedTransport([(503, b"busy", {}) for _ in range(MAX_ATTEMPTS)])
    patch_client(httpx, t)
    svc = make_service()
    with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
        try:
            await svc.embed_single("hello", use_cache=False)
            check("Persistent 5xx raises EmbeddingServiceError", False, "no exception")
        except EmbeddingServiceError as e:
            check(
                f"Persistent 5xx gives up after {MAX_ATTEMPTS} attempts",
                t.calls == MAX_ATTEMPTS and "503" in str(e),
                f"{t.calls} calls, msg={e}",
            )


async def _async_transport_error_exhausts_retries():
    t = ScriptedRaiseTransport(httpx.RemoteProtocolError("conn reset"))
    patch_client(httpx, t)
    svc = make_service()
    with mock.patch("asyncio.sleep", new=mock.AsyncMock()):
        try:
            await svc.embed_single("hello", use_cache=False)
            check("Persistent transport error raises", False, "no exception")
        except EmbeddingServiceError as e:
            check(
                f"Persistent transport error gives up after {MAX_ATTEMPTS} attempts",
                t.calls == MAX_ATTEMPTS and "unreachable" in str(e).lower(),
                f"{t.calls} calls, msg={e}",
            )


# ---------- batching + shared cache connection (ADR-009 step 2) --------------

async def _mk_cache_db(db_path: str) -> None:
    """Create the embedding_cache table in a temp-file SQLite database."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS embedding_cache ("
            " text_hash TEXT PRIMARY KEY, text TEXT NOT NULL,"
            " embedding BLOB NOT NULL, model TEXT NOT NULL,"
            " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        await db.commit()


async def _seed_corrupt_row(db_path: str, text: str, model: str) -> None:
    """Pre-seed a legacy pickle blob (0x80 prefix) row for ``text``."""
    text_hash = hashlib.md5(text.encode()).hexdigest()
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO embedding_cache "
            "(text_hash, text, embedding, model) VALUES (?, ?, ?, ?)",
            (text_hash, text, b"\x80garbage-not-json", model),
        )
        await db.commit()


async def _async_query_path_single_call():
    """retrieve() now embeds the deduped query list (rewrite + variants, <=4
    texts) via embed_batch — it must cost ONE /embeddings request, not 1+N."""
    t = RecordingTransport([
        (200, json.dumps({"data": [
            {"embedding": [0.1, 0.1, 0.1, 0.1]},
            {"embedding": [0.2, 0.2, 0.2, 0.2]},
            {"embedding": [0.3, 0.3, 0.3, 0.3]},
            {"embedding": [0.4, 0.4, 0.4, 0.4]},
        ]}).encode(), {"content-type": "application/json"}),
    ])
    patch_client(httpx, t)
    svc = make_service()
    embs = await svc.embed_batch(["q1", "q2", "q3", "q4"], use_cache=False)
    check(
        "query path: 4 texts → ONE /embeddings request, 4 vectors back",
        t.calls == 1 and len(embs) == 4,
        f"{t.calls} calls, {len(embs)} vectors",
    )
    body = t.requests[0]
    check(
        "request body: input is a 4-element list, dimensions + model set",
        isinstance(body["input"], list) and len(body["input"]) == 4
        and body["dimensions"] == 4 and body["model"] == "test-model",
        f"input={type(body['input']).__name__}, keys={sorted(body)}",
    )


async def _async_cache_roundtrip_shared_conn():
    """The shared aiosqlite connection must make the cache actually work
    across calls (and self-heal corrupt rows) with zero transport calls."""
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="kg-emb-cache-"), "cache.db"
    )
    await _mk_cache_db(db_path)
    t = ScriptedTransport([
        (200, json.dumps({"data": [
            {"embedding": [0.1, 0.2, 0.3, 0.4]},
            {"embedding": [0.5, 0.6, 0.7, 0.8]},
        ]}).encode(), {"content-type": "application/json"}),
    ])
    patch_client(httpx, t)
    svc = make_service(db_path=db_path)
    embs1 = await svc.embed_batch(["hello", "world"], use_cache=True)
    check(
        "first round: 1 call, 2 vectors",
        t.calls == 1 and len(embs1) == 2,
        f"{t.calls} calls",
    )
    embs2 = await svc.embed_batch(["hello", "world"], use_cache=True)
    check(
        "second round: served from shared-connection cache, 0 new calls",
        t.calls == 1 and embs2 == embs1,
        f"{t.calls} calls",
    )
    await svc.close()

    # Corrupt-row self-heal on the shared connection.
    await _seed_corrupt_row(db_path, "corrupt-me", "test-model")
    t2 = ScriptedTransport([
        (200, json.dumps({"data": [
            {"embedding": [0.9, 0.9, 0.9, 0.9]},
        ]}).encode(), {"content-type": "application/json"}),
    ])
    patch_client(httpx, t2)
    svc2 = make_service(db_path=db_path)
    healed = await svc2.embed_batch(["corrupt-me"], use_cache=True)
    check(
        "corrupt (pickle) row self-heals: deleted, re-embedded",
        t2.calls == 1 and healed == [[0.9, 0.9, 0.9, 0.9]],
        f"{t2.calls} calls, {healed!r}",
    )
    again = await svc2.embed_batch(["corrupt-me"], use_cache=True)
    check(
        "healed row now served from cache",
        t2.calls == 1 and again == [[0.9, 0.9, 0.9, 0.9]],
        f"{t2.calls} calls",
    )
    await svc2.close()


async def _async_batch_size_from_config():
    """Ingest batching scales with settings.EMBED_BATCH_SIZE: 5 texts at
    batch size 2 → 3 API calls with 2 inter-batch delays between them."""
    t = ScriptedTransport([
        (200, json.dumps({"data": [
            {"embedding": [0.1, 0, 0, 0]}, {"embedding": [0.2, 0, 0, 0]},
        ]}).encode(), {"content-type": "application/json"}),
        (200, json.dumps({"data": [
            {"embedding": [0.3, 0, 0, 0]}, {"embedding": [0.4, 0, 0, 0]},
        ]}).encode(), {"content-type": "application/json"}),
        (200, json.dumps({"data": [
            {"embedding": [0.5, 0, 0, 0]},
        ]}).encode(), {"content-type": "application/json"}),
    ])
    patch_client(httpx, t)
    svc = make_service(batch_size=2, batch_delay=0.05)
    with mock.patch("asyncio.sleep", new=mock.AsyncMock()) as zzz:
        embs = await svc.embed_batch([f"t{i}" for i in range(5)], use_cache=False)
    check(
        "EMBED_BATCH_SIZE=2: 5 texts → 3 API calls",
        t.calls == 3 and len(embs) == 5,
        f"{t.calls} calls, {len(embs)} vectors",
    )
    check(
        "2 inter-batch sleeps, each using EMBED_BATCH_DELAY_SECONDS",
        zzz.await_count == 2 and all(
            a.args and a.args[0] == 0.05 for a in zzz.await_args_list
        ),
        f"{zzz.await_count} sleeps",
    )


# ---------------------------------------------------------------------------
# Dual-mode entry points.
#
# The async checks above are prefixed ``_async_`` (not ``test_``) so pytest
# never collects them as bare coroutines (which pytest-asyncio in strict mode
# would silently SKIP with a PytestUnhandledCoroutineWarning — those retry
# tests were effectively never running under ``pytest tests/``). Each is
# exposed here as a sync ``test_*`` wrapper that runs it via asyncio.run and
# asserts on the collected failures, so pytest actually fails on a regression.
# The standalone runner (``python tests/test_embedding.py``) keeps the
# original print-based report.
# ---------------------------------------------------------------------------


def _run_async(fn) -> None:
    """Run one async check; fail the pytest item if any `check` failed."""
    _failures.clear()
    try:
        asyncio.run(fn())
    finally:
        # patch_client() mutates the httpx.AsyncClient module attribute and
        # never restores it; restore here so the last wrapper doesn't leave
        # a scripted transport in place for later-collected test modules.
        httpx.AsyncClient = _ORIGINAL_ASYNC_CLIENT
    assert not _failures, "; ".join(_failures)


def test_retry_schedule_matches_spec():
    _failures.clear()
    check(
        "Retry budget: MAX_ATTEMPTS == 5, 429 failover handled by key pool",
        MAX_ATTEMPTS == 5,
    )
    assert not _failures, "; ".join(_failures)


async def _async_429_fails_over_to_second_key_without_sleep():
    """The pool's core value: a 429 on one key must cool THAT key down and
    retry immediately on another — no fixed-delay sleep in between."""
    ok = json.dumps({"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]}).encode()
    t = RecordingTransport([
        (429, b'{"error":"rate limited"}', {"content-type": "application/json",
                                            "retry-after": "5"}),
        (200, ok, {"content-type": "application/json"}),
    ])
    patch_client(httpx, t)
    svc = make_service(pool=KeyPool(["test-key-aaaa", "test-key-bbbb"],
                                    per_key_concurrency=5))
    with mock.patch("asyncio.sleep", new=mock.AsyncMock()) as zzz:
        emb = await svc.embed_single("hello", use_cache=False)
    check(
        "429 on key A → immediate failover to key B, vector returned",
        emb == [0.1, 0.2, 0.3, 0.4] and t.calls == 2
        and t.auth_headers[0] != t.auth_headers[1]
        and "aaaa" in t.auth_headers[0] and "bbbb" in t.auth_headers[1],
        f"{t.calls} calls, auth={t.auth_headers}",
    )
    check(
        "failover did NOT sleep",
        zzz.await_count == 0, f"{zzz.await_count} sleeps",
    )


def test_retry_then_succeed_on_5xx():
    _run_async(_async_retry_then_succeed_on_5xx)


def test_4xx_fast_fail():
    _run_async(_async_4xx_fast_fail)


def test_5xx_exhausts_retries():
    _run_async(_async_5xx_exhausts_retries)


def test_transport_error_exhausts_retries():
    _run_async(_async_transport_error_exhausts_retries)


def test_query_path_single_call():
    _run_async(_async_query_path_single_call)


def test_cache_roundtrip_shared_conn():
    _run_async(_async_cache_roundtrip_shared_conn)


def test_batch_size_from_config():
    _run_async(_async_batch_size_from_config)


def test_429_fails_over_to_second_key_without_sleep():
    _run_async(_async_429_fails_over_to_second_key_without_sleep)


if __name__ == "__main__":
    print("\nRetry / backoff")
    check(
        "Retry schedule matches spec (1, 2, 4, 8, 16s, 5 attempts)",
        RETRY_DELAYS_SECONDS == [1, 2, 4, 8, 16] and MAX_ATTEMPTS == 5,
    )

    asyncio.run(_async_retry_then_succeed_on_5xx())
    asyncio.run(_async_4xx_fast_fail())
    asyncio.run(_async_5xx_exhausts_retries())
    asyncio.run(_async_transport_error_exhausts_retries())
    asyncio.run(_async_query_path_single_call())
    asyncio.run(_async_cache_roundtrip_shared_conn())
    asyncio.run(_async_batch_size_from_config())
    asyncio.run(_async_429_fails_over_to_second_key_without_sleep())

    print()
    if _failures:
        print(f"\033[91m{len(_failures)} FAILED:\033[0m " + ", ".join(_failures))
        sys.exit(1)
    print(f"\033[92mAll checks passed.\033[0m")
