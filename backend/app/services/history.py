"""Bounded conversation-history loading and compaction (GUIDE-003 T2-2).

Cost and answer quality must not degrade linearly with conversation age.
Old turns fold into a single ``role='summary'`` row; every prompt then
receives "summary + recent window" instead of a naive tail. The summary row
is itself the durable recovery boundary — the same idea as codex-rs rollout's
CompactedItem checkpoints (GUIDE-003 T2-2).

Storage convention (no schema change — messages.role is free TEXT):

    A role='summary' row represents the fold of ALL history before its
    created_at. Invariants (enforced by this module's write path):
      * at most ONE live summary row per conversation;
      * no normal message exists after it with an earlier position.

Both invariants rely on AUTOINCREMENT id monotonicity, which is why every
query here orders by id, never by created_at (ties break deterministically).

The compaction hook is fire-and-forget: a failed summary only logs and the
next turn retries. Chat latency is never blocked on it.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional

from app.config import get_settings
from app.prompts import load_prompt

logger = logging.getLogger(__name__)

# Role value marking a folded-history row. Not exposed to clients: the
# transcript endpoint filters it out and load_chat_history maps it to a
# system-role prompt entry.
SUMMARY_ROLE = "summary"

# Prompt-side mapping: a summary row enters the LLM message list as a system
# turn. Mapping it to assistant/user would corrupt strict role alternation
# that some providers enforce.
_SUMMARY_PROMPT_ROLE = "system"


async def load_chat_history(
    db,
    conversation_id: str,
    limit: int,
    include_summary: bool = True,
) -> List[Dict[str, str]]:
    """Load the bounded chat window for one conversation.

    Returns at most ``limit`` normal messages plus, when available and useful,
    one leading summary entry mapped to the system role. Order is strictly
    chronological.

    The summary rides along only when the recent window doesn't already cover
    the whole conversation: if the kept tail reaches back past the summary
    row, the raw turns are strictly more informative than their own fold, so
    the summary would be redundant payload.
    """
    # Newest-first over ids (monotonic), then reversed once below.
    async with db.execute(
        "SELECT id, role, content FROM messages "
        "WHERE conversation_id = ? AND role != ? "
        "ORDER BY id DESC LIMIT ?",
        (conversation_id, SUMMARY_ROLE, limit),
    ) as cursor:
        rows = await cursor.fetchall()

    entries: List[Dict[str, str]] = []
    oldest_loaded_id = rows[-1]["id"] if rows else None

    if include_summary and rows:
        async with db.execute(
            # Summary must be OLDER than the loaded window to add anything;
            # `id < oldest_loaded_id` expresses exactly that.
            "SELECT content FROM messages "
            "WHERE conversation_id = ? AND role = ? AND id < ? "
            "ORDER BY id DESC LIMIT 1",
            (conversation_id, SUMMARY_ROLE, oldest_loaded_id),
        ) as cursor:
            summary_row = await cursor.fetchone()
        if summary_row:
            entries.append({
                "role": _SUMMARY_PROMPT_ROLE,
                "content": f"（此前对话的摘要）{summary_row['content']}",
            })

    entries.extend(
        {"role": r["role"], "content": r["content"]} for r in reversed(rows)
    )
    return entries


async def maybe_compact_history(conversation_id: str) -> None:
    """Fold old turns into a summary row when the threshold is crossed.

    Fire-and-forget by contract: callers wrap in asyncio.create_task. All
    failure paths log and return without raising — compaction must never take
    down a chat turn whose answer already streamed successfully.

    Threshold semantics: compaction considers the segment AFTER the latest
    summary row (or the whole conversation when none exists). When that
    segment exceeds HISTORY_COMPACT_THRESHOLD messages, everything except the
    newest HISTORY_KEEP_RECENT folds into one new summary; the old summary is
    merged in and deleted, preserving the one-live-summary invariant.
    """
    settings = get_settings()
    if not settings.HISTORY_COMPACT_ENABLED:
        return

    try:
        await _compact_once(conversation_id, settings)
    except Exception as e:  # noqa: BLE001 - fire-and-forget must not raise
        logger.warning(
            "history compaction skipped for conversation %s: %s",
            conversation_id, e,
        )


async def _compact_once(conversation_id: str, settings) -> None:
    """One threshold check + fold attempt. Raises on infrastructure errors."""
    from app.database import get_db

    keep_recent = max(0, settings.HISTORY_KEEP_RECENT)

    async with get_db() as db:
        async with db.execute(
            "SELECT id FROM messages WHERE conversation_id = ? AND role = ? "
            "ORDER BY id DESC LIMIT 1",
            (conversation_id, SUMMARY_ROLE),
        ) as cursor:
            summary_row = await cursor.fetchone()
        summary_id: Optional[int] = (
            summary_row["id"] if summary_row else None
        )

        # Fold candidates are ALL normal messages, not just those after the
        # last summary: the previous round deliberately left KEEP_RECENT raw
        # messages BELOW the summary row, and a later fold must absorb them —
        # otherwise the replacement summary would not represent the full
        # history before it (invariant break).
        async with db.execute(
            "SELECT COUNT(*) FROM messages "
            "WHERE conversation_id = ? AND role != ?",
            (conversation_id, SUMMARY_ROLE),
        ) as cursor:
            normal_count = (await cursor.fetchone())[0]

        if normal_count <= settings.HISTORY_COMPACT_THRESHOLD:
            return

        # Oldest messages get folded; the newest KEEP_RECENT stay raw so
        # immediate follow-ups keep full fidelity.
        async with db.execute(
            "SELECT id, role, content FROM messages "
            "WHERE conversation_id = ? AND role != ? "
            "ORDER BY id ASC",
            (conversation_id, SUMMARY_ROLE),
        ) as cursor:
            segment_rows = await cursor.fetchall()

        fold_rows = segment_rows[:len(segment_rows) - keep_recent]
        if not fold_rows:
            return  # degenerate: nothing left to fold this round

        # The old summary text participates in the merge so information from
        # earlier rounds survives the replacement.
        old_summary_text = ""
        if summary_id is not None:
            async with db.execute(
                "SELECT content FROM messages WHERE id = ?",
                (summary_id,),
            ) as cursor:
                old_summary_text = (await cursor.fetchone())["content"]

        transcript = _render_transcript(old_summary_text, fold_rows)
        summary_text = await _summarize(transcript)

        # Write path keeps the invariant: insert new summary first, then drop
        # the old row + folded messages. A crash between the two statements
        # leaves BOTH summaries present; the next run picks the newer one by
        # id and self-heals (the stale older row gets deleted then too).
        await db.execute(
            "INSERT INTO messages (conversation_id, role, content) "
            "VALUES (?, ?, ?)",
            (conversation_id, SUMMARY_ROLE, summary_text),
        )
        if summary_id is not None:
            await db.execute("DELETE FROM messages WHERE id = ?", (summary_id,))
        await db.executemany(
            "DELETE FROM messages WHERE id = ?",
            [(r["id"],) for r in fold_rows],
        )
        await db.commit()

    logger.info(
        "history compacted for conversation %s: %d messages folded",
        conversation_id, len(fold_rows),
    )


def _render_transcript(old_summary_text: str, fold_rows) -> str:
    """Render the LLM-side transcript for one compaction round."""
    blocks: List[str] = []
    if old_summary_text:
        blocks.append(f"（更早的旧摘要）\n{old_summary_text}")
    blocks.extend(f"{r['role']}: {r['content']}" for r in fold_rows)
    return "\n\n".join(blocks)


async def _summarize(transcript: str) -> str:
    """LLM-summarize one transcript via the history_compact template."""
    from app.services.llm import get_llm_service

    prompt = load_prompt("history_compact", transcript=transcript)
    llm = await get_llm_service()
    summary = await llm.chat_complete(
        [{"role": "user", "content": prompt}],
        temperature=0.2,
        max_tokens=600,
        enable_thinking=False,
    )
    summary = (summary or "").strip()
    max_chars = get_settings().HISTORY_SUMMARY_MAX_CHARS
    if len(summary) > max_chars:
        summary = summary[:max_chars].rstrip()
    if not summary:
        raise ValueError("empty summary from LLM")
    return summary


# Strong references to fire-and-forget compaction tasks. asyncio keeps only
# weak references to running tasks — without this set a freshly-spawned task
# can be garbage-collected (silently cancelled) at its first await. The
# done-callback discards entries so the set doesn't grow forever.
_pending_tasks: set = set()


def spawn_history_compaction(conversation_id: str) -> Optional[asyncio.Task]:
    """Schedule maybe_compact_history off the request path.

    Returns the Task so tests can await it deterministically; None when
    disabled or no running loop (e.g. under a sync test harness). Never
    raises — failures inside maybe_compact_history only log.
    """
    settings = get_settings()
    if not settings.HISTORY_COMPACT_ENABLED:
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(maybe_compact_history(conversation_id))
    _pending_tasks.add(task)
    task.add_done_callback(_pending_tasks.discard)
    return task
