"""Tests for the shared HTTP retry policy (app/services/retry.py)."""
import asyncio

import httpx
import pytest

from app.services.key_pool import KeyPool
from app.services.retry import (
    RETRYABLE_EXCEPTIONS,
    RETRYABLE_STATUS_CODES,
    backoff_delay,
    run_with_retry,
)


# ---------- backoff_delay ----------

class TestBackoffDelay:
    def test_exponential_growth(self):
        assert backoff_delay(0, 1.0) == pytest.approx(1.0, abs=0.11)
        d2 = backoff_delay(1, 1.0)
        assert 1.8 <= d2 <= 2.2          # base*2 ±10%
        d3 = backoff_delay(2, 1.0)
        assert 3.6 <= d3 <= 4.4          # base*4 ±10%

    def test_jitter_stays_in_band(self):
        for attempt in range(6):
            expected = 1.0 * (2 ** min(attempt, 5))
            got = backoff_delay(attempt, 1.0)
            assert expected * 0.9 <= got <= expected * 1.1

    def test_attempt_saturates_at_5(self):
        # absurdly large attempt must not explode (bounded growth)
        assert backoff_delay(50, 1.0) == pytest.approx(32.0, rel=0.15)


# ---------- classification ----------

class TestClassification:
    def test_retryable_status_codes_content(self):
        assert {408, 425, 429, 500, 502, 503, 504} == set(RETRYABLE_STATUS_CODES)

    @pytest.mark.parametrize("exc_cls", [
        httpx.RemoteProtocolError,
        httpx.ConnectError,
        httpx.ReadTimeout,
        httpx.WriteTimeout,
        httpx.PoolTimeout,
        httpx.LocalProtocolError,
    ])
    def test_retryable_exceptions_are_httpx_transport(self, exc_cls):
        assert exc_cls in RETRYABLE_EXCEPTIONS


# ---------- run_with_retry ----------

def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/x")
    return httpx.HTTPStatusError(
        f"status {code}", request=request,
        response=httpx.Response(code, request=request),
    )


class TestRunWithRetry:
    @pytest.mark.asyncio
    async def test_success_first_try_no_sleep(self, monkeypatch):
        sleeps: list[float] = []

        async def _fake_sleep(s):
            sleeps.append(s)

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
        calls = {"n": 0}

        async def fn():
            calls["n"] += 1
            return "ok"

        assert await run_with_retry("op", fn, max_attempts=3) == "ok"
        assert calls["n"] == 1
        assert sleeps == []

    @pytest.mark.asyncio
    async def test_retryable_then_success(self, monkeypatch):
        sleeps: list[float] = []

        async def _fake_sleep(s):
            sleeps.append(s)

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
        attempts = {"n": 0}

        async def fn():
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _status_error(503)
            return "recovered"

        out = await run_with_retry("op", fn, max_attempts=3, base_delay=1.0)
        assert out == "recovered"
        assert attempts["n"] == 2
        assert len(sleeps) == 1
        assert 0.9 <= sleeps[0] <= 1.1   # first gap = base ±jitter

    @pytest.mark.asyncio
    async def test_non_retryable_status_raises_immediately(self, monkeypatch):
        async def _fail_sleep(_s):  # pragma: no cover - must never run
            raise AssertionError("sleep called for non-retryable error")

        monkeypatch.setattr(asyncio, "sleep", _fail_sleep)
        attempts = {"n": 0}

        async def fn():
            attempts["n"] += 1
            raise _status_error(404)

        with pytest.raises(httpx.HTTPStatusError):
            await run_with_retry("op", fn, max_attempts=3)
        assert attempts["n"] == 1

    @pytest.mark.asyncio
    async def test_exhaustion_raises_last_error(self, monkeypatch):
        async def _noop_sleep(_s):
            return None

        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
        attempts = {"n": 0}

        async def fn():
            attempts["n"] += 1
            raise _status_error(500)

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await run_with_retry("op", fn, max_attempts=3)
        assert exc_info.value.response.status_code == 500
        assert attempts["n"] == 3

    @pytest.mark.asyncio
    async def test_transport_exception_retried(self, monkeypatch):
        async def _noop_sleep(_s):
            return None

        monkeypatch.setattr(asyncio, "sleep", _noop_sleep)
        attempts = {"n": 0}

        async def fn():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise httpx.ReadTimeout("timed out")
            return "up"

        assert await run_with_retry("op", fn, max_attempts=5) == "up"
        assert attempts["n"] == 3

    @pytest.mark.asyncio
    async def test_explicit_delays_schedule_used(self, monkeypatch):
        sleeps: list[float] = []

        async def _fake_sleep(s):
            sleeps.append(s)

        monkeypatch.setattr(asyncio, "sleep", _fake_sleep)
        attempts = {"n": 0}

        async def fn():
            attempts["n"] += 1
            if attempts["n"] < 4:
                raise _status_error(429)
            return True

        assert await run_with_retry(
            "op", fn, max_attempts=5, delays=[1, 2, 4],
        )
        assert sleeps == [1.0, 2.0, 4.0]

    @pytest.mark.asyncio
    async def test_unexpected_exception_not_retried(self, monkeypatch):
        async def _fail_sleep(_s):  # pragma: no cover - must never run
            raise AssertionError("sleep called for non-retryable error")

        monkeypatch.setattr(asyncio, "sleep", _fail_sleep)
        attempts = {"n": 0}

        async def fn():
            attempts["n"] += 1
            raise KeyError("malformed payload")

        with pytest.raises(KeyError):
            await run_with_retry("op", fn, max_attempts=3)
        assert attempts["n"] == 1


# ---------- reranker integration ----------

class TestRerankerRetry:
    @pytest.mark.asyncio
    async def test_rerank_falls_back_after_retries_and_logs(self, caplog):
        """Persistent rerank failure falls back to input order — and the
        degradation is logged (it used to be silent)."""
        import logging as _logging

        from app.services.reranker import RerankService

        svc = RerankService.__new__(RerankService)
        svc.base_url = "https://example.test"
        svc._pool = KeyPool(["fake-key"], per_key_concurrency=2)
        svc.model = "fake-rerank"

        client_calls = {"n": 0}

        class _FlakyClient:
            async def post(self, *a, **kw):
                client_calls["n"] += 1
                raise httpx.ConnectError("down")

        svc._client = _FlakyClient()

        chunks = [{"chunk_id": "a", "content": "first"},
                  {"chunk_id": "b", "content": "second"}]
        with caplog.at_level(_logging.WARNING, logger="app.services.reranker"):
            out = await svc.rerank("q", chunks, top_k=2)

        assert [c["chunk_id"] for c in out] == ["a", "b"]
        assert all("relevance_score" not in c for c in out)
        assert client_calls["n"] == 3      # max_attempts=3 through shared policy
        assert any("falling back" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_rerank_recovers_on_transient_failure(self, monkeypatch):
        from app.services.reranker import RerankService

        svc = RerankService.__new__(RerankService)
        svc.base_url = "https://example.test"
        svc._pool = KeyPool(["fake-key"], per_key_concurrency=2)
        svc.model = "fake-rerank"

        responses = ["fail", {
            "results": [{"index": 1, "relevance_score": 0.7},
                        {"index": 0, "relevance_score": 0.3}],
        }]

        class _Once503ThenOk:
            async def post(self, url, headers=None, json=None):
                item = responses.pop(0)
                if item == "fail":
                    request = httpx.Request("POST", url)
                    raise httpx.HTTPStatusError(
                        "503", request=request,
                        response=httpx.Response(503, request=request),
                    )
                request = httpx.Request("POST", url)
                return httpx.Response(200, json=item, request=request)

        svc._client = _Once503ThenOk()
        monkeypatch.setattr(asyncio, "sleep", _async_noop)

        chunks = [{"chunk_id": "a", "content": "first"},
                  {"chunk_id": "b", "content": "second"}]
        out = await svc.rerank("q", chunks, top_k=2)
        assert [c["chunk_id"] for c in out] == ["b", "a"]
        assert out[0]["relevance_score"] == 0.7


async def _async_noop(_seconds):
    return None
