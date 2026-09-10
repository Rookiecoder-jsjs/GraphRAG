"""Global admission gate for the retrieval hot path (chat + search).

Under overload the alternative to admitting everyone is degrading everyone:
rerank fallbacks, dropped graph channels, skipped variants — all silent
quality losses that scale WITH load (see ADR-009). This gate implements the
opposite policy: **queue up to ``QUERY_MAX_QUEUE_SECONDS``, then reject**.
Accepted retrievals always run the full-quality pipeline.

The gate is acquired INSIDE ``retrieve()`` *after* a retrieval-cache miss —
cache hits never consume capacity or queue.

Per-process scope, mirroring ``ingest_gate``: with multiple uvicorn workers
each process admits up to ``QUERY_CONCURRENCY`` of its own retrievals.

Implementation note: this is deliberately NOT ``asyncio.Semaphore`` +
``wait_for`` — on Python 3.11 a cancelled ``Semaphore.acquire()`` can lose a
permit (fixed in 3.12). Here the counter is only mutated while the condition
lock is held, with no ``await`` between the eligibility check and the
increment, and a waiter cancelled inside ``wait()`` re-acquires the lock in
``wait()``'s internal ``finally`` without ever touching the counter — a
cancelled waiter structurally cannot consume a slot.
"""
import asyncio
import logging
import math
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from app.config import get_settings

logger = logging.getLogger(__name__)

# Single source for the user-facing busy message (search 429 detail and the
# chat SSE error frame both carry it).
_BUSY_MESSAGE = "当前查询压力较大，请稍后重试"


class QueryGateTimeout(Exception):
    """Raised when a retrieval could not be admitted within the queue budget.

    ``retry_after`` is the server's honest estimate of how long the client
    should wait before retrying (the queue budget it just exhausted).
    """

    def __init__(self, retry_after: float, message: str = _BUSY_MESSAGE) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class _QueryGate:
    """Counter + condition admission gate with a bounded wait queue."""

    def __init__(self, limit: int, max_queue_seconds: float) -> None:
        self._limit = max(1, int(limit))
        self._max_wait = max(0.0, float(max_queue_seconds))
        self._active = 0
        self._cond = asyncio.Condition()

    @property
    def active(self) -> int:
        return self._active

    @property
    def limit(self) -> int:
        return self._limit

    async def acquire(self) -> None:
        """Take a slot, queueing up to ``max_queue_seconds``.

        Raises ``QueryGateTimeout`` (with a ``retry_after`` hint) when the
        queue budget expires before a slot frees up.
        """
        deadline = time.monotonic() + self._max_wait
        async with self._cond:
            while self._active >= self._limit:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise QueryGateTimeout(
                        retry_after=max(1.0, math.ceil(self._max_wait))
                    )
                try:
                    await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                except asyncio.TimeoutError:
                    raise QueryGateTimeout(
                        retry_after=max(1.0, math.ceil(self._max_wait))
                    ) from None
            self._active += 1

    async def release(self) -> None:
        async with self._cond:
            self._active = max(0, self._active - 1)
            # Wake ALL waiters: a woken one may time out without consuming,
            # so a single notify() could leave a freed slot unclaimed while
            # everyone sleeps on. notify_all keeps this trivially correct.
            self._cond.notify_all()

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """Hold one admission slot for the duration of the block."""
        await self.acquire()
        try:
            yield
        finally:
            await self.release()


_gate: Optional[_QueryGate] = None


def get_query_gate() -> _QueryGate:
    """Return the process-wide query gate (lazily built from config)."""
    global _gate
    if _gate is None:
        settings = get_settings()
        _gate = _QueryGate(
            limit=settings.QUERY_CONCURRENCY,
            max_queue_seconds=settings.QUERY_MAX_QUEUE_SECONDS,
        )
    return _gate


def reset_query_gate() -> None:
    """Drop the cached gate so the next ``get_query_gate`` rebuilds it (tests)."""
    global _gate
    _gate = None
