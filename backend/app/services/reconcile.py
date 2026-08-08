"""Reconcile documents stuck in non-terminal processing states.

A document can stall in a checkpoint (pending / document_created / indexed
/ graphed) if the background pipeline crashed mid-flight. This scans for
such docs whose last progress_history event is older than a stale window
(or absent entirely) and marks them ``failed`` so they surface in the UI
instead of lingering as "in progress" forever.

Run as a non-blocking startup task (see main.py lifespan). Safe to re-run:
it only touches docs still in a non-terminal state.
"""
import logging
from datetime import datetime, timedelta

from app.database import get_db
from app.services.doc_status import DocStatus

logger = logging.getLogger(__name__)

# Non-terminal states; `ready` and `failed` are terminal and never touched.
_STUCK_STATES = (
    DocStatus.PENDING.value,
    DocStatus.DOCUMENT_CREATED.value,
    DocStatus.INDEXED.value,
    DocStatus.GRAPHED.value,
)
# A doc with no progress event in this window is presumed abandoned.
_STALE_MINUTES = 30


async def reconcile_stuck_documents() -> int:
    """Mark abandoned in-flight documents as failed. Returns the count marked.

    A doc is "stuck" if it's in a non-terminal state AND its last
    progress_history event is older than the stale window (or it has no
    progress rows at all - meaning the pipeline never reported).
    """
    cutoff = (datetime.utcnow() - timedelta(minutes=_STALE_MINUTES)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    placeholders = ",".join("?" * len(_STUCK_STATES))
    async with get_db() as db:
        async with db.execute(
            f"""
            SELECT d.id, d.user_id, d.status
            FROM documents d
            LEFT JOIN (
                SELECT doc_id, MAX(created_at) AS last_event
                FROM progress_history GROUP BY doc_id
            ) p ON p.doc_id = d.id
            WHERE d.status IN ({placeholders})
              AND (p.last_event IS NULL OR p.last_event < ?)
            """,
            (*_STUCK_STATES, cutoff),
        ) as cur:
            rows = await cur.fetchall()

        count = 0
        for row in rows:
            # Re-check status in the UPDATE WHERE clause to avoid racing a
            # concurrent pipeline that may have advanced the doc since the
            # SELECT. rowcount reflects whether we actually changed it.
            cur2 = await db.execute(
                f"""UPDATE documents
                    SET status = ?, error_message = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ? AND status IN ({placeholders})""",
                (
                    DocStatus.FAILED.value,
                    "Reconciled: stuck in non-terminal state",
                    row[0],
                    *_STUCK_STATES,
                ),
            )
            count += cur2.rowcount
            logger.warning(
                "Reconciled stuck doc %s (was %s) for user_id=%s",
                row[0], row[2], row[1],
            )
        await db.commit()
    if count:
        logger.info("Reconcile: marked %d stuck document(s) as failed", count)
    return count
