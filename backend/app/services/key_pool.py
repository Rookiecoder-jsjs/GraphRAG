"""SiliconFlow multi-key pool shared by the embedding and rerank services.

Provider rate limits (RPM/TPM/concurrency) are enforced per API key, so N
keys multiply the usable quota. The pool leases the least-busy key that is
neither cooling down (recent 429) nor over its in-flight cap; the caller
wraps each provider call in ``run_with_key_retry`` which reacts to a 429 by
cooling that key down and failing over to an idle one *without sleeping*.

Deliberately absent from this first version: proactive per-key RPM sliding
windows. Reactive 429 cooldown + failover already covers the near-limit
case, and an estimate that drifts from the provider's real counter would
just queue requests that would have succeeded.

Key material must never reach the logs — every log line uses
``fingerprint()`` (``****`` + last 4 chars) instead.

The pool is per-process state (mirrors ``ingest_gate``): with multiple
uvicorn workers each process leases from the same configured key set and the
provider merges the counts server-side, which is correct.
"""
import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, List, Optional, Set, TypeVar

import httpx

from app.config import get_settings
from app.services.retry import (
    RETRYABLE_EXCEPTIONS,
    RETRYABLE_STATUS_CODES,
    backoff_delay,
)

logger = logging.getLogger(__name__)

# Cooldown applied to a 429'd key when the response carries no usable
# numeric Retry-After header.
DEFAULT_COOLDOWN_SECONDS = 15.0
MIN_COOLDOWN_SECONDS = 1.0
_FINGERPRINT_LEN = 4

T = TypeVar("T")


class KeyPoolError(Exception):
    """Base error for key-pool failures."""


class KeyPoolExhausted(KeyPoolError):
    """No key could be leased within the timeout (all cooling or capped)."""


class _KeyState:
    """Mutable per-key bookkeeping; guarded by the pool's asyncio.Condition."""

    __slots__ = ("key", "inflight", "cooldown_until")

    def __init__(self, key: str) -> None:
        self.key = key
        self.inflight = 0
        self.cooldown_until = 0.0  # time.monotonic() deadline


class KeyPool:
    """Least-inflight lease scheduler over N API keys.

    ``lease()`` grants a key only when it is below ``per_key_concurrency``
    in-flight calls and not cooling down; otherwise the caller waits (bounded
    by ``lease_timeout``) for a release or cooldown expiry. The in-flight
    counter is incremented between the eligibility check and the return with
    no ``await`` in between while the condition lock is held, so a cancelled
    waiter can never consume a slot (the Python 3.11 ``asyncio.Semaphore``
    cancellation race is avoided structurally, not patched around).
    """

    def __init__(
        self,
        keys: List[str],
        per_key_concurrency: int,
        lease_timeout: float = 20.0,
    ) -> None:
        self._states = [_KeyState(k) for k in keys]
        self._cap = max(1, int(per_key_concurrency))
        self._lease_timeout = max(0.1, float(lease_timeout))
        self._cond = asyncio.Condition()
        self._logged = False

    @property
    def size(self) -> int:
        return len(self._states)

    def fingerprint(self, key: str) -> str:
        """Log-safe identity: ``****`` + last 4 chars, never the full key."""
        if not key or len(key) <= _FINGERPRINT_LEN:
            return "****"
        return "****" + key[-_FINGERPRINT_LEN:]

    def _pick(self, now: float) -> Optional[int]:
        """Index of the least-inflight eligible key, or None. O(n), n tiny."""
        best: Optional[int] = None
        for i, st in enumerate(self._states):
            if st.inflight >= self._cap or st.cooldown_until > now:
                continue
            if best is None or st.inflight < self._states[best].inflight:
                best = i
        return best

    def has_idle_key(self) -> bool:
        """True when at least one key could be leased right now."""
        return self._pick(time.monotonic()) is not None

    def seconds_until_next_release(self) -> float:
        """Seconds until the earliest cooldown expires; 0.0 if none cooling."""
        now = time.monotonic()
        remaining = [
            st.cooldown_until - now
            for st in self._states
            if st.cooldown_until > now
        ]
        return max(0.0, min(remaining)) if remaining else 0.0

    def report_rate_limited(self, key: str, retry_after: Optional[float]) -> None:
        """Put a 429'd key into cooldown.

        The cooldown is clamped to ``lease_timeout``: with a single key,
        honouring an unbounded Retry-After would make the pool give up
        (exhaust) *earlier* than the old fixed-delay retry ever did.
        """
        raw = retry_after if (retry_after is not None and retry_after > 0) \
            else DEFAULT_COOLDOWN_SECONDS
        cooldown = min(max(raw, MIN_COOLDOWN_SECONDS), self._lease_timeout)
        now = time.monotonic()
        for st in self._states:
            if st.key == key:
                st.cooldown_until = max(st.cooldown_until, now + cooldown)
                break
        logger.warning(
            "KeyPool: key %s cooling down %.1fs after 429 (keys=%d)",
            self.fingerprint(key), cooldown, self.size,
        )

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[str]:
        """Acquire the least-busy eligible key; release on exit (even on error).

        Raises ``KeyPoolExhausted`` immediately when the pool was built with
        zero keys (unconfigured deployment fails fast instead of hanging), or
        after waiting ``lease_timeout`` with every key capped or cooling.
        """
        async with self._cond:
            if not self._logged:
                self._logged = True
                logger.info(
                    "KeyPool: %d key(s), per-key cap %d", self.size, self._cap
                )
            if not self._states:
                raise KeyPoolExhausted("no SiliconFlow API keys configured")
            deadline = time.monotonic() + self._lease_timeout
            while True:
                idx = self._pick(time.monotonic())
                if idx is not None:
                    self._states[idx].inflight += 1
                    key = self._states[idx].key
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    cooling = sum(
                        1 for st in self._states
                        if st.cooldown_until > time.monotonic()
                    )
                    raise KeyPoolExhausted(
                        f"no SiliconFlow key leasable in "
                        f"{self._lease_timeout:.0f}s "
                        f"(keys={self.size}, cooling={cooling})"
                    )
                # Wake on the next release if sooner than the deadline;
                # a timeout here just re-runs the eligibility check.
                wake = min(remaining, self.seconds_until_next_release() or remaining)
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=wake)
                except asyncio.TimeoutError:
                    continue
        try:
            yield key
        finally:
            async with self._cond:
                for st in self._states:
                    if st.key == key:
                        st.inflight = max(0, st.inflight - 1)
                        break
                self._cond.notify_all()


def parse_keys(settings) -> List[str]:
    """Extract the pool's keys from settings.

    ``SILICON_FLOW_API_KEYS`` is comma-separated (order preserved, deduped,
    blanks dropped); when it parses to nothing the single legacy
    ``SILICON_FLOW_API_KEY`` is used so existing .env files keep working.
    """
    raw = (getattr(settings, "SILICON_FLOW_API_KEYS", "") or "").strip()
    keys: List[str] = []
    if raw:
        seen: Set[str] = set()
        for part in raw.split(","):
            k = part.strip()
            if k and k not in seen:
                seen.add(k)
                keys.append(k)
    if not keys:
        single = (getattr(settings, "SILICON_FLOW_API_KEY", "") or "").strip()
        if single:
            keys = [single]
    return keys


_key_pool: Optional[KeyPool] = None


def get_key_pool() -> KeyPool:
    """Return the process-wide SiliconFlow pool (lazily built from config)."""
    global _key_pool
    if _key_pool is None:
        settings = get_settings()
        _key_pool = KeyPool(
            parse_keys(settings),
            per_key_concurrency=settings.SILICON_FLOW_PER_KEY_CONCURRENCY,
            lease_timeout=settings.SILICON_FLOW_KEY_LEASE_TIMEOUT,
        )
    return _key_pool


def reset_key_pool() -> None:
    """Drop the cached pool so the next ``get_key_pool`` rebuilds it (tests)."""
    global _key_pool
    _key_pool = None


def _retry_after_seconds(response: Optional[httpx.Response]) -> Optional[float]:
    """Numeric Retry-After (delta-seconds) from a 429 response, else None.

    HTTP-date form and garbage both yield None — the caller falls back to
    the default cooldown rather than guessing at clock math.
    """
    if response is None:
        return None
    raw = response.headers.get("retry-after")
    if not raw:
        return None
    try:
        value = float(raw.strip())
    except ValueError:
        return None
    return value if value >= 0 else None


async def run_with_key_retry(
    op_name: str,
    pool: KeyPool,
    attempt_fn: "Callable[[str], Awaitable[T]]",
    *,
    max_attempts: int = 4,
    base_delay: float = 1.0,
) -> T:
    """Run ``attempt_fn(key)`` with pool-aware retry and 429 failover.

    ``attempt_fn`` receives a leased key and must build its own headers from
    it (a fresh key is chosen per attempt). Semantics:

    - **429**: the key is cooled down; when another key is idle the next
      attempt starts immediately (no sleep — the whole point of the pool).
      With no idle key the wait is ``min(retry_after, backoff)``; the lease
      then waits out any *remaining* cooldown, so patience is bounded by the
      Retry-After and never doubled.
    - **Other retryable statuses** (5xx, 408, ...) and transport errors:
      shared exponential backoff with jitter (``backoff_delay``).
    - **Non-retryable errors** and ``KeyPoolExhausted``: propagate unchanged
      (the caller owns the translation into its service error).
    """
    last_error: Optional[BaseException] = None
    for attempt in range(max_attempts):
        key: Optional[str] = None
        try:
            async with pool.lease() as key:
                return await attempt_fn(key)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else None
            if status == 429 and key is not None:
                pool.report_rate_limited(key, _retry_after_seconds(e.response))
                if attempt < max_attempts - 1 and pool.has_idle_key():
                    logger.warning(
                        "%s: 429 on key %s — failing over without sleep",
                        op_name, pool.fingerprint(key),
                    )
                    last_error = e
                    continue
                last_error = e  # no idle key → timed path below
            elif status is None or status not in RETRYABLE_STATUS_CODES:
                raise
            else:
                last_error = e
        except RETRYABLE_EXCEPTIONS as e:
            last_error = e

        if attempt >= max_attempts - 1:
            break
        delay = backoff_delay(attempt, base_delay)
        response = last_error.response \
            if isinstance(last_error, httpx.HTTPStatusError) else None
        if response is not None and response.status_code == 429:
            retry_after = _retry_after_seconds(response)
            if retry_after is not None:
                delay = min(retry_after, delay)
        logger.warning(
            "%s failed (attempt %d/%d, key=%s), retrying in %.1fs: %.200s",
            op_name, attempt + 1, max_attempts,
            pool.fingerprint(key) if key else "-",
            delay, last_error,
        )
        await asyncio.sleep(delay)
    assert last_error is not None
    raise last_error
