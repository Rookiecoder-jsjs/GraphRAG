"""Tests for the eval_cases API + feedback→case conversion (FEAT-018).

Endpoints are exercised directly as coroutines (suite-wide pattern). Covered:
table + PARTIAL unique index existence with idempotent init; manual create/
list roundtrip; the from-message conversion (query = preceding user message,
expected_chunk_ids = message_sources ordered by rank with NULLs last); the
unique-index-backed idempotency of conversion under repeat calls; ownership
404s; PATCH/DELETE.
"""
import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.eval import (
    EvalCaseCreate,
    EvalCaseFromMessage,
    EvalCaseUpdate,
    convert_from_message,
    create_case,
    delete_case,
    list_cases,
    update_case,
)
from app.database import get_db, init_db


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    """Throwaway SQLite per test (suite-wide pattern)."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "eval_test.db"))
    from app.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


async def _bootstrap():
    """user 1 + a conversation: user msg 10 → assistant msg 11 with sources
    (rank 2 / rank 1 / rank NULL) so conversion ordering is observable."""
    await init_db()
    async with get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'u1', 'x')"
        )
        await db.execute(
            "INSERT INTO conversations (id, user_id, title) VALUES ('conv-1', 1, 't')"
        )
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES (10, 'conv-1', 'user', '什么是知识图谱？')"
        )
        await db.execute(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES (11, 'conv-1', 'assistant', '回答 [1] [2]')"
        )
        await db.executemany(
            "INSERT INTO message_sources (message_id, chunk_id, rank) VALUES (?, ?, ?)",
            [(11, "chunk-b", 2), (11, "chunk-a", 1), (11, "chunk-c", None)],
        )
        await db.commit()


def test_table_and_partial_unique_index_exist_and_init_idempotent():
    async def main():
        await init_db()
        async with get_db() as db:
            async with db.execute("PRAGMA table_info(eval_cases)") as cur:
                cols = {r["name"] for r in await cur.fetchall()}
            assert {"id", "user_id", "source_message_id", "query",
                    "expected_chunk_ids", "expected_keywords", "tags",
                    "enabled"} <= cols
            async with db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='uq_eval_cases_source'"
            ) as cur:
                assert await cur.fetchone() is not None
        await init_db()  # second run must not blow up

    asyncio.run(main())


def test_create_and_list_case_roundtrip():
    async def main():
        await _bootstrap()
        created = await create_case(
            body=EvalCaseCreate(
                query="什么是知识图谱？",
                expected_keywords=["实体", "关系"],
                tags=["manual"],
            ),
            current_user={"id": 1},
        )
        assert created["id"] > 0
        assert created["query"] == "什么是知识图谱？"
        assert created["expected_chunk_ids"] == []
        assert created["expected_keywords"] == ["实体", "关系"]
        assert created["enabled"] is True
        assert created["source_message_id"] is None

        rows = await list_cases(current_user={"id": 1})
        assert len(rows) == 1
        assert rows[0]["id"] == created["id"]

        foreign = await list_cases(current_user={"id": 2})
        assert foreign == []

    asyncio.run(main())


def test_create_case_requires_query():
    with pytest.raises(ValidationError):
        EvalCaseCreate(query="   ")


def test_from_message_uses_preceding_user_message_and_ranked_sources():
    async def main():
        await _bootstrap()
        result = await convert_from_message(
            body=EvalCaseFromMessage(message_id=11),
            current_user={"id": 1},
        )
        assert result["created"] is True
        assert result["query"] == "什么是知识图谱？"
        # message_sources ordered by rank, NULLs last.
        assert result["expected_chunk_ids"] == ["chunk-a", "chunk-b", "chunk-c"]
        assert "user-feedback" in result["tags"]
        assert result["source_message_id"] == 11

    asyncio.run(main())


def test_from_message_idempotent_returns_existing():
    async def main():
        await _bootstrap()
        first = await convert_from_message(
            body=EvalCaseFromMessage(message_id=11), current_user={"id": 1}
        )
        second = await convert_from_message(
            body=EvalCaseFromMessage(message_id=11), current_user={"id": 1}
        )
        assert first["created"] is True
        assert second["created"] is False
        assert second["id"] == first["id"]

        async with get_db() as db:
            async with db.execute("SELECT COUNT(*) AS n FROM eval_cases") as cur:
                assert (await cur.fetchone())["n"] == 1

    asyncio.run(main())


def test_from_message_404_foreign_or_missing():
    async def main():
        await _bootstrap()
        for caller, message_id in ((2, 11), (1, 999)):
            with pytest.raises(HTTPException) as exc:
                await convert_from_message(
                    body=EvalCaseFromMessage(message_id=message_id),
                    current_user={"id": caller},
                )
            assert exc.value.status_code == 404

    asyncio.run(main())


def test_from_message_409_when_no_preceding_user_message():
    async def main():
        await init_db()
        async with get_db() as db:
            await db.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'u1', 'x')"
            )
            await db.execute(
                "INSERT INTO conversations (id, user_id, title) VALUES ('conv-2', 1, 't')"
            )
            # Assistant message with NO user turn before it.
            await db.execute(
                "INSERT INTO messages (id, conversation_id, role, content) "
                "VALUES (20, 'conv-2', 'assistant', '回答')"
            )
            await db.commit()

        with pytest.raises(HTTPException) as exc:
            await convert_from_message(
                body=EvalCaseFromMessage(message_id=20), current_user={"id": 1}
            )
        assert exc.value.status_code == 409

    asyncio.run(main())


def test_patch_and_delete_case_and_ownership():
    async def main():
        await _bootstrap()
        created = await create_case(
            body=EvalCaseCreate(query="q1"), current_user={"id": 1}
        )

        updated = await update_case(
            case_id=created["id"],
            body=EvalCaseUpdate(enabled=False, expected_answer="要点"),
            current_user={"id": 1},
        )
        assert updated["enabled"] is False
        assert updated["expected_answer"] == "要点"
        assert updated["query"] == "q1"  # untouched field survives PATCH

        # Foreign caller must not see or mutate the case.
        with pytest.raises(HTTPException) as exc:
            await update_case(
                case_id=created["id"],
                body=EvalCaseUpdate(enabled=True),
                current_user={"id": 2},
            )
        assert exc.value.status_code == 404
        with pytest.raises(HTTPException) as exc:
            await delete_case(case_id=created["id"], current_user={"id": 2})
        assert exc.value.status_code == 404

        await delete_case(case_id=created["id"], current_user={"id": 1})
        with pytest.raises(HTTPException) as exc:
            await delete_case(case_id=created["id"], current_user={"id": 1})
        assert exc.value.status_code == 404

    asyncio.run(main())
