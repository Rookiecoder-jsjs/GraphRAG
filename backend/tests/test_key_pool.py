"""Tests for app.services.key_pool — SiliconFlow multi-key lease + retry.

The pool is the concurrency-expansion primitive behind ADR-009: N keys
multiply provider quota, and the pool's core value is the 429 reaction —
cool the key down, fail over to an idle key WITHOUT sleeping, and only
fall back to timed backoff when every key is unavailable. The tests pin:

- key parsing (comma list, dedupe, legacy single-key fallback);
- least-inflight selection and the per-key in-flight cap;
- cooldown bookkeeping (Retry-After handling, clamp to lease_timeout,
  expiry);
- cancel-safety: a cancelled waiter must never consume a slot (the
  Python 3.11 asyncio.Semaphore race is avoided by construction);
- run_with_key_retry's 429 failover with zero sleeps;
- log-safe fingerprints (no key material in log lines).
"""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from unittest import mock

from app.services.key_pool import (
    KeyPool,
    KeyPoolExhausted,
    parse_keys,
    run_with_key_retry,
)


# ------------------------------------------------------------- key parsing

def test_parse_keys_dedupes_and_strips():
    s = SimpleNamespace(SILICON_FLOW_API_KEYS=" k1 , k2,k1, ,k3 ", SILICON_FLOW_API_KEY="legacy")
    assert parse_keys(s) == ["k1", "k2", "k3"]


def test_parse_keys_falls_back_to_single_key():
    s = SimpleNamespace(SILICON_FLOW_API_KEYS="", SILICON_FLOW_API_KEY="legacy")
    assert parse_keys(s) == ["legacy"]


def test_parse_keys_empty_when_both_unset():
    s = SimpleNamespace(SILICON_FLOW_API_KEYS="", SILICON_FLOW_API_KEY="")
    assert parse_keys(s) == []


# ------------------------------------------------------------ basic leasing

@pytest.mark.asyncio
async def test_empty_pool_fails_fast_with_clear_error():
    pool = KeyPool([], per_key_concurrency=5)
    with pytest.raises(KeyPoolExhausted, match="no SiliconFlow API keys"):
        async with pool.lease():
            pass


@pytest.mark.asyncio
async def test_least_inflight_balances_across_keys():
    pool = KeyPool(["a", "b", "c"], per_key_concurrency=10)
    peak = {k: 0 for k in "abc"}
    current = {k: 0 for k in "abc"}

    async def worker():
        async with pool.lease() as k:
            current[k] += 1
            peak[k] = max(peak[k], current[k])
            await asyncio.sleep(0.02)
            current[k] -= 1

    await asyncio.gather(*[worker() for _ in range(9)])
    # 9 tasks over 3 keys: least-inflight keeps peaks within one of each other.
    assert max(peak.values()) - min(peak.values()) <= 1, peak
    assert all(v == 0 for v in current.values())


@pytest.mark.asyncio
async def test_per_key_cap_bounds_concurrency():
    pool = KeyPool(["only"], per_key_concurrency=3)
    inflight = 0
    peak = 0

    async def worker():
        nonlocal inflight, peak
        async with pool.lease():
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.01)
            inflight -= 1

    results = await asyncio.gather(*[worker() for _ in range(12)])
    assert results == [None] * 12
    assert peak == 3, f"peak {peak} != cap 3"


@pytest.mark.asyncio
async def test_lease_timeout_raises_when_key_held():
    pool = KeyPool(["only"], per_key_concurrency=1, lease_timeout=0.05)

    async def hold():
        async with pool.lease():
            await asyncio.sleep(0.5)

    holder = asyncio.create_task(hold())
    await asyncio.sleep(0.01)  # let the holder take the only slot
    with pytest.raises(KeyPoolExhausted):
        async with pool.lease():
            pass
    holder.cancel()
    await asyncio.gather(holder, return_exceptions=True)


# ---------------------------------------------------------------- cooldown

@pytest.mark.asyncio
async def test_rate_limit_cools_single_key_and_expires():
    pool = KeyPool(["k"], per_key_concurrency=5, lease_timeout=20.0)
    pool.report_rate_limited("k", 1.0)
    assert not pool.has_idle_key()
    assert 0 < pool.seconds_until_next_release() <= 1.1
    await asyncio.sleep(1.1)
    assert pool.has_idle_key()


@pytest.mark.asyncio
async def test_rate_limit_one_key_does_not_idle_other():
    pool = KeyPool(["k1", "k2"], per_key_concurrency=5)
    pool.report_rate_limited("k1", 30)
    assert pool.has_idle_key()  # k2 is still leasable


@pytest.mark.asyncio
async def test_cooldown_clamped_to_lease_timeout():
    pool = KeyPool(["k"], per_key_concurrency=5, lease_timeout=2.0)
    pool.report_rate_limited("k", 999)
    # 999s demand must clamp to the 2s lease timeout, so the pool never
    # gives up sooner than the lease it granted patience for.
    assert 0 < pool.seconds_until_next_release() <= 2.1


@pytest.mark.asyncio
async def test_missing_retry_after_uses_default_cooldown():
    pool = KeyPool(["k"], per_key_concurrency=5, lease_timeout=20.0)
    pool.report_rate_limited("k", None)
    remaining = pool.seconds_until_next_release()
    assert 14.0 < remaining <= 15.0  # DEFAULT_COOLDOWN_SECONDS


# ------------------------------------------------------------ cancel safety

@pytest.mark.asyncio
async def test_cancelled_waiters_never_leak_slots():
    pool = KeyPool(["k1", "k2"], per_key_concurrency=1, lease_timeout=5.0)
    done_bodies = 0

    async def worker(i: int):
        nonlocal done_bodies
        async with pool.lease():
            done_bodies += 1
            await asyncio.sleep(0.01)

    tasks = [asyncio.create_task(worker(i)) for i in range(10)]
    await asyncio.sleep(0.005)  # 2 leases granted, 8 queued
    for t in tasks[2:7]:
        t.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    cancelled = sum(1 for r in results if isinstance(r, asyncio.CancelledError))
    assert cancelled == 5
    assert done_bodies == 5  # 2 holders + 3 surviving waiters
    # Every lease was released: the pool is fully idle again.
    for st in pool._states:
        assert st.inflight == 0
    assert pool.has_idle_key()


# ------------------------------------------------- run_with_key_retry / 429

def _http_429(retry_after: str) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://example.test/v1/embeddings")
    response = httpx.Response(
        429, headers={"Retry-After": retry_after}, request=request
    )
    return httpx.HTTPStatusError("429", request=request, response=response)


@pytest.mark.asyncio
async def test_429_fails_over_without_sleep():
    pool = KeyPool(["key-aaaa", "key-bbbb"], per_key_concurrency=5)
    seen_keys: list = []

    async def attempt_fn(key: str):
        seen_keys.append(key)
        if len(seen_keys) == 1:
            raise _http_429("5")
        return "ok"

    with mock.patch("asyncio.sleep", new=mock.AsyncMock()) as zzz:
        result = await run_with_key_retry("embed", pool, attempt_fn)
    assert result == "ok"
    assert len(seen_keys) == 2 and seen_keys[0] != seen_keys[1]
    assert zzz.await_count == 0, "failover must not sleep"
    # The 429'd key is cooling; the retry already used the other one.
    assert pool.seconds_until_next_release() > 0


@pytest.mark.asyncio
async def test_429_with_no_idle_key_waits_out_cooldown_then_retries():
    pool = KeyPool(["only"], per_key_concurrency=5)
    calls: list = []

    async def attempt_fn(key: str):
        calls.append(key)
        if len(calls) == 1:
            raise _http_429("1.0")
        return "ok"

    result = await run_with_key_retry("embed", pool, attempt_fn)
    assert result == "ok"
    assert len(calls) == 2
    # Same single key reused after its cooldown expired.
    assert calls[0] == calls[1]


@pytest.mark.asyncio
async def test_non_retryable_status_raises_immediately():
    pool = KeyPool(["k"], per_key_concurrency=5)
    request = httpx.Request("POST", "https://example.test/v1/embeddings")
    response = httpx.Response(401, request=request)

    async def attempt_fn(key: str):
        raise httpx.HTTPStatusError("401", request=request, response=response)

    with pytest.raises(httpx.HTTPStatusError):
        await run_with_key_retry("embed", pool, attempt_fn)


@pytest.mark.asyncio
async def test_retryable_5xx_uses_backoff_sleep():
    pool = KeyPool(["k"], per_key_concurrency=5)
    calls: list = []

    async def attempt_fn(key: str):
        calls.append(key)
        if len(calls) == 1:
            request = httpx.Request("POST", "https://example.test/v1/x")
            response = httpx.Response(503, request=request)
            raise httpx.HTTPStatusError("503", request=request, response=response)
        return "ok"

    with mock.patch("asyncio.sleep", new=mock.AsyncMock()) as zzz:
        result = await run_with_key_retry("embed", pool, attempt_fn)
    assert result == "ok" and len(calls) == 2
    assert zzz.await_count == 1  # one timed retry for the 503


# ------------------------------------------------------------- fingerprints

def test_fingerprint_never_leaks_key_material():
    pool = KeyPool(["sk-abcdefgh"], per_key_concurrency=5)
    fp = pool.fingerprint("sk-abcdefgh")
    assert fp == "****efgh"
    assert "abcdefgh" not in fp and "sk-abcdefgh" not in fp
    assert pool.fingerprint("abc") == "****"
    assert pool.fingerprint("") == "****"
