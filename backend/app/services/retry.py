"""Shared HTTP retry policy for outbound LLM/embedding/rerank calls.

One definition of "what is worth retrying" and "how long to wait", consumed
by every service that talks to a model-provider HTTP API. Modeled on
codex-client's retry policy (codex-rs/codex-client/src/retry.rs): exponential
backoff with ±10% jitter so concurrent failures don't re-synchronize.

Classification (identical across services — it used to be duplicated three
ways and drift):
  - retryable status codes: 408/425/429 + 5xx (4xx won't fix themselves)
  - retryable exceptions: transport-level httpx errors only
  - everything else fails fast
"""
import asyncio
import logging
import random
from typing import Awaitable, Callable, Optional, Sequence, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Status codes where an immediate repeat has a real chance of succeeding:
# request timeouts, too-early, rate limit, and upstream server errors.
RETRYABLE_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})

# Transport-level errors: the request never got a meaningful response, so a
# retry is safe. Deliberately does NOT include ConnectTimeout — a connect
# timeout usually means the endpoint itself is down, which no sleep fixes.
RETRYABLE_EXCEPTIONS = (
    httpx.RemoteProtocolError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.LocalProtocolError,
)

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0


def backoff_delay(attempt: int, base_delay: float) -> float:
    """Exponential backoff with jitter: ``base * 2^attempt * U(0.9, 1.1)``.

    ``attempt`` is zero-based (the delay BEFORE the second try is base*1).
    Jitter spreads out retries that would otherwise land on the same tick —
    codex-client uses the same ±10% band.
    """
    exp = min(attempt, 5)  # saturate so absurd attempt counts stay bounded
    raw = base_delay * (2 ** exp)
    return raw * random.uniform(0.9, 1.1)


async def run_with_retry(
    op_name: str,
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY_SECONDS,
    delays: Optional[Sequence[float]] = None,
) -> T:
    """Run ``fn()`` retrying on retryable transport/HTTP failures.

    Args:
        op_name: label for log lines (e.g. "rerank").
        fn: zero-arg async callable. Called fresh each attempt so closures
            rebuild their payload (lets callers drop rejected params between
            attempts).
        max_attempts: total tries including the first (default 3).
        base_delay: exponential base when ``delays`` is not given.
        delays: explicit per-gap schedule overriding exponential backoff
            (delays[i] waits before attempt i+1). Kept for embedding.py's
            existing [1,2,4,8,16] contract; new call sites should use
            max_attempts/base_delay instead.

    Raises:
        The last exception, chained via ``from`` so the original failure
        stays visible. Non-retryable failures raise immediately.
    """
    last_error: Optional[BaseException] = None
    for attempt in range(max_attempts):
        try:
            return await fn()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code if e.response is not None else None
            if status is None or status not in RETRYABLE_STATUS_CODES:
                raise
            last_error = e
        except RETRYABLE_EXCEPTIONS as e:
            last_error = e

        if attempt >= max_attempts - 1:
            break
        if delays is not None:
            delay = float(delays[min(attempt, len(delays) - 1)])
        else:
            delay = backoff_delay(attempt, base_delay)
        logger.warning(
            "%s failed (attempt %d/%d), retrying in %.1fs: %.200s",
            op_name, attempt + 1, max_attempts, delay, last_error,
        )
        await asyncio.sleep(delay)

    assert last_error is not None  # loop only exits via return or raise above
    raise last_error
