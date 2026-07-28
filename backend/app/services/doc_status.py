"""Document processing state machine.

The ``documents.status`` column is the single source of truth for "how far has
this document been processed?". Unlike ``progress_history`` (a fine-grained
event log for the progress UI), ``status`` records only *durable checkpoints* —
states whose work has actually been persisted to the backing stores (Neo4j /
Chroma / SQLite). That distinction is deliberate: it is what makes future
crash-recovery / resume possible, because on restart we can read the checkpoint
and skip work that is already durable.

Lifecycle (monotonic, forward-only)::

    pending -> document_created -> indexed -> graphed -> ready
                    (any non-terminal state) -> failed

Transition rules enforced by :func:`set_document_status`:

* forward move          -> applied
* same state            -> idempotent no-op (returns False)
* backward move         -> idempotent no-op (resume/retry safe, returns False)
* non-terminal -> failed-> applied, records ``error_message``
* leaving a terminal    -> raises :class:`InvalidStatusTransition`
  (``ready``/``failed``)  (use :func:`reset_for_retry` to reprocess a failed doc)
"""
import logging
from enum import Enum
from typing import Optional

from app.database import get_db

logger = logging.getLogger(__name__)


class DocStatus(str, Enum):
    """Durable checkpoints in a document's processing lifecycle."""

    PENDING = "pending"                    # row created; background work not started
    DOCUMENT_CREATED = "document_created"  # Neo4j Document node exists
    INDEXED = "indexed"                    # chunks in Chroma + SQLite (+ BM25)
    GRAPHED = "graphed"                    # chunk nodes/links in Neo4j
    READY = "ready"                        # entities + relations saved; complete
    FAILED = "failed"                      # terminal error (see error_message)


# Forward ordering of the non-terminal checkpoint states. ``failed`` is handled
# separately because it is reachable from anywhere and has no rank.
_RANK = {
    DocStatus.PENDING: 0,
    DocStatus.DOCUMENT_CREATED: 1,
    DocStatus.INDEXED: 2,
    DocStatus.GRAPHED: 3,
    DocStatus.READY: 4,
}

#: States from which no further transition is allowed without an explicit reset.
TERMINAL_STATES = frozenset({DocStatus.READY, DocStatus.FAILED})


class InvalidStatusTransition(Exception):
    """Raised when a transition would leave a terminal state."""

    def __init__(self, current: str, target: str) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"illegal document status transition: {current!r} -> {target!r}"
        )


class DocumentNotFound(Exception):
    """Raised when a status update targets a non-existent document."""

    def __init__(self, doc_id: str) -> None:
        self.doc_id = doc_id
        super().__init__(f"document not found: {doc_id!r}")


def _coerce(status) -> DocStatus:
    """Accept a DocStatus or its string value; reject unknowns loudly."""
    return DocStatus(status)


def validate_transition(current, target) -> bool:
    """Return True if ``current -> target`` should be written.

    Raises :class:`InvalidStatusTransition` for moves out of a terminal state.
    Returns False for idempotent no-ops (same or backward move) so the caller
    can skip the write.
    """
    current = _coerce(current)
    target = _coerce(target)

    if current == target:
        return False  # idempotent

    if current is DocStatus.FAILED:
        # Re-failing is a no-op; anything else needs an explicit retry reset.
        if target is DocStatus.FAILED:
            return False
        raise InvalidStatusTransition(current.value, target.value)

    if current is DocStatus.READY:
        raise InvalidStatusTransition(current.value, target.value)

    if target is DocStatus.FAILED:
        return True  # any non-terminal state may fail

    # Both are ranked checkpoint states: forward applies, backward is a no-op.
    if _RANK[target] <= _RANK[current]:
        logger.debug(
            "ignoring backward document status move %s -> %s",
            current.value, target.value,
        )
        return False
    return True


async def set_document_status(
    doc_id: str,
    status,
    *,
    error_message: Optional[str] = None,
) -> bool:
    """Idempotently move a document to ``status``.

    Returns True if the row was updated, False if it was a no-op. Clears
    ``error_message`` on any successful (non-failed) transition; records it on
    a transition to ``failed``.
    """
    target = _coerce(status)

    async with get_db() as db:
        async with db.execute(
            "SELECT status FROM documents WHERE id = ?", (doc_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise DocumentNotFound(doc_id)

        current = row["status"] or DocStatus.PENDING.value
        if not validate_transition(current, target):
            return False

        if target is DocStatus.FAILED:
            await db.execute(
                "UPDATE documents "
                "SET status = ?, error_message = ?, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (target.value, error_message, doc_id),
            )
        else:
            await db.execute(
                "UPDATE documents "
                "SET status = ?, error_message = NULL, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (target.value, doc_id),
            )
        await db.commit()

    logger.info("document %s status -> %s", doc_id, target.value)
    return True


async def reset_for_retry(doc_id: str) -> bool:
    """Move a ``failed`` document back to ``pending`` so it can be reprocessed.

    Only valid from ``failed``; raises :class:`InvalidStatusTransition`
    otherwise (a ``ready`` document is not retried via this path).
    """
    async with get_db() as db:
        async with db.execute(
            "SELECT status FROM documents WHERE id = ?", (doc_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise DocumentNotFound(doc_id)

        current = _coerce(row["status"] or DocStatus.PENDING.value)
        if current is not DocStatus.FAILED:
            raise InvalidStatusTransition(current.value, DocStatus.PENDING.value)

        await db.execute(
            "UPDATE documents "
            "SET status = ?, error_message = NULL, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            (DocStatus.PENDING.value, doc_id),
        )
        await db.commit()
    logger.info("document %s reset for retry", doc_id)
    return True


async def get_document_status(doc_id: str) -> Optional[str]:
    """Return the current status string for a document, or None if missing."""
    async with get_db() as db:
        async with db.execute(
            "SELECT status FROM documents WHERE id = ?", (doc_id,)
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return row["status"] or DocStatus.PENDING.value
