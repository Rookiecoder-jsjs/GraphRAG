"""Lightweight in-memory sliding-window rate limiter (no external dependencies).

Used to throttle authentication endpoints (login/register) against brute-force
and account-spraying attacks, and the billable chat/search endpoints against
quota-burning loops. State lives in process memory, so limits are per-worker
and reset on restart - adequate as a first line of defence for a single-instance
deployment. Swap for a shared store (e.g. Redis) if the app ever runs multiple
workers behind a load balancer.
"""
import time
from collections import defaultdict, deque
from typing import Deque, Dict

from fastapi import HTTPException, status


class SlidingWindowLimiter:
    """Allow at most ``max_calls`` hits per ``key`` within ``window_seconds``."""

    def __init__(self, max_calls: int, window_seconds: int) -> None:
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)

    def _purge(self, key: str, now: float) -> Deque[float]:
        dq = self._hits[key]
        cutoff = now - self.window_seconds
        while dq and dq[0] <= cutoff:
            dq.popleft()
        return dq

    def is_allowed(self, key: str) -> bool:
        """Record a hit for ``key`` and return whether it is within the limit."""
        now = time.monotonic()
        dq = self._purge(key, now)
        if len(dq) >= self.max_calls:
            return False
        dq.append(now)
        # Opportunistic memory guard: drop keys whose NEWEST hit has expired.
        # Sprayed keys are never re-checked, so their deques never self-purge
        # via _purge - without this an attacker spraying unique identifiers
        # could grow the dict without bound. (Checking the newest hit is
        # enough: it is the youngest, so if even it expired, all have.)
        if len(self._hits) > 10000:
            cutoff = now - self.window_seconds
            for k in [k for k, v in self._hits.items() if v and v[-1] <= cutoff]:
                del self._hits[k]
        return True

    def retry_after(self, key: str) -> int:
        """Seconds until the oldest hit for ``key`` leaves the window."""
        dq = self._hits.get(key)
        if not dq:
            return 0
        elapsed = time.monotonic() - dq[0]
        return max(1, int(self.window_seconds - elapsed) + 1)


# Login: a handful of attempts per (IP, username) per minute stops online
# brute force while tolerating honest mistypes.
login_limiter = SlidingWindowLimiter(max_calls=8, window_seconds=60)
# Registration: tight per-IP to blunt mass account creation (each account
# triggers billable LLM/embedding work on first upload).
register_limiter = SlidingWindowLimiter(max_calls=10, window_seconds=3600)
# Chat: each call fans out into billable LLM + embedding + rerank work, so
# throttle per user to cap a single account's spend rate. 20/min is well
# above any honest read pace but stops a tight loop from burning provider
# quota.
chat_limiter = SlidingWindowLimiter(max_calls=20, window_seconds=60)
# Search: cheaper than chat (no generation) but still hits embedding +
# rerank, so allow a slightly higher rate.
search_limiter = SlidingWindowLimiter(max_calls=30, window_seconds=60)


def enforce_rate_limit(limiter: SlidingWindowLimiter, key: str) -> None:
    """Raise 429 when ``key`` exceeds ``limiter``. No-op under test env.

    Centralised so every throttled endpoint (auth/chat/search) applies the
    same test-env bypass and Retry-After header without re-implementing it.
    """
    from app.config import get_settings
    if get_settings().APP_ENV.lower() in ("test", "testing"):
        return
    if not limiter.is_allowed(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please slow down.",
            headers={"Retry-After": str(limiter.retry_after(key))},
        )
