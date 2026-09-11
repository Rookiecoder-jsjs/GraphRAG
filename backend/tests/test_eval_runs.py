"""Tests for eval run persistence (FEAT-021): eval/db_runs.py + runner --save
+ GET /api/eval/runs.

The table is append-only evidence behind 军规②: one row per ``eval.runner
--save`` run, aggregate summary as JSON TEXT, no per-case rows. save_run is
best-effort — any sqlite failure must degrade to None + a stderr warning,
never break the eval run itself.
"""
import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from app.database import get_db, init_db


@pytest.fixture(autouse=True)
def tmp_sqlite(monkeypatch, tmp_path):
    """Throwaway SQLite per test (suite-wide pattern)."""
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "eval_runs_test.db"))
    from app.config import get_settings

    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _db_path() -> Path:
    from app.config import get_settings

    return Path(get_settings().SQLITE_PATH)


def _save(**overrides) -> int:
    from eval.db_runs import save_run

    kwargs = dict(
        label="baseline",
        mode="no-llm",
        config={"use_graph_rag": False, "k_values": [1, 3, 5], "gold_dir": "gold"},
        summary={"hit@1_mean": 0.5, "hit@1_n": 2, "mrr_mean": 0.75, "mrr_n": 2},
        total_cases=2,
    )
    kwargs.update(overrides)
    return save_run(_db_path(), kwargs.pop("user_id", 1), **kwargs)


async def _bootstrap_users(*user_ids):
    await init_db()
    async with get_db() as db:
        for uid in user_ids:
            await db.execute(
                "INSERT OR IGNORE INTO users (id, username, password_hash) "
                f"VALUES ({int(uid)}, 'u{int(uid)}', 'x')"
            )
        await db.commit()


def test_init_db_creates_eval_runs_table_and_index():
    async def main():
        await init_db()
        await init_db()  # idempotent

    asyncio.run(main())
    conn = sqlite3.connect(str(_db_path()))
    cols = {row[1] for row in conn.execute("PRAGMA table_info(eval_runs)")}
    assert {
        "id", "user_id", "label", "mode", "config",
        "summary", "total_cases", "created_at",
    } <= cols
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(eval_runs)")}
    assert "idx_eval_runs_user" in indexes
    conn.close()


def test_save_run_roundtrip():
    asyncio.run(init_db())
    run_id = _save()
    run_id2 = _save(label="after-rewrite", mode="llm")
    assert run_id == 1 and run_id2 == 2

    conn = sqlite3.connect(str(_db_path()))
    row = conn.execute(
        "SELECT user_id, label, mode, config, summary, total_cases, created_at "
        "FROM eval_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    conn.close()
    assert row[0] == 1
    assert row[1] == "baseline" and row[2] == "no-llm"
    assert json.loads(row[3])["k_values"] == [1, 3, 5]
    assert json.loads(row[4])["mrr_mean"] == 0.75
    assert row[5] == 2
    assert row[6]  # created_at stamped


def test_save_run_missing_table_returns_none(tmp_path):
    # A bare sqlite file without the app schema: standalone `python -m
    # eval.runner` against an uninitialized DB must not crash.
    db_file = tmp_path / "bare.db"
    conn = sqlite3.connect(str(db_file))
    conn.close()
    from eval.db_runs import save_run

    result = save_run(
        db_file, 1, label="", mode="no-llm", config={}, summary={}, total_cases=0
    )
    assert result is None


def test_save_run_unwritable_path_returns_none(tmp_path):
    from eval.db_runs import save_run

    result = save_run(
        tmp_path / "no" / "such" / "dir.db", 1,
        label="", mode="no-llm", config={}, summary={}, total_cases=0,
    )
    assert result is None


def test_list_runs_desc_order_json_parsed_and_user_isolated():
    from app.api.eval_runs import list_runs
    from eval.db_runs import save_run

    asyncio.run(_bootstrap_users(1, 2))
    _save(label="first")
    _save(label="second")
    save_run(
        _db_path(), 2, label="foreign", mode="llm",
        config={"use_graph_rag": True}, summary={"hit@1_mean": 0.1}, total_cases=1,
    )

    runs = asyncio.run(list_runs(current_user={"id": 1}, limit=50))
    assert [r["id"] for r in runs] == [2, 1]  # newest first
    assert runs[0]["label"] == "second"
    assert runs[0]["config"] == {
        "use_graph_rag": False, "k_values": [1, 3, 5], "gold_dir": "gold",
    }
    assert isinstance(runs[0]["summary"], dict)
    assert runs[0]["total_cases"] == 2
    # User 2's run must be invisible to user 1.
    assert all(r["label"] != "foreign" for r in runs)


def test_list_runs_respects_limit():
    from app.api.eval_runs import list_runs

    asyncio.run(init_db())
    for label in ("a", "b", "c"):
        _save(label=label)
    runs = asyncio.run(list_runs(current_user={"id": 1}, limit=1))
    assert len(runs) == 1
    assert runs[0]["label"] == "c"


def _write_gold(tmp_path) -> Path:
    gold = tmp_path / "gold"
    gold.mkdir()
    (gold / "case.json").write_text(
        json.dumps({
            "id": "smoke-1",
            "query": "什么是智能体？",
            "expected_chunk_ids": ["g1"],
            "expected_keywords": [],
            "expected_answer": "",
            "difficulty": "easy",
            "tags": [],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    return gold


def _patch_retriever(monkeypatch):
    async def _fake_factory(user_id, use_graph_rag=False):
        async def _retrieve(query, top_k):
            return ["g1"]

        return _retrieve

    monkeypatch.setattr(
        "eval.runner._build_rag_context_retriever", _fake_factory
    )


def test_runner_main_async_save_records_run(tmp_path, monkeypatch):
    from eval.runner import main_async

    asyncio.run(_bootstrap_users(1))
    gold = _write_gold(tmp_path)
    _patch_retriever(monkeypatch)

    exit_code = asyncio.run(main_async([
        "--no-llm", "--no-db", "--save", "--label", "t1",
        "--gold-dir", str(gold), "--user-id", "1",
    ]))
    assert exit_code == 0

    conn = sqlite3.connect(str(_db_path()))
    rows = conn.execute(
        "SELECT label, mode, config, summary, total_cases FROM eval_runs"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    label, mode, config_raw, summary_raw, total_cases = rows[0]
    assert label == "t1" and mode == "no-llm"
    config = json.loads(config_raw)
    assert config["use_graph_rag"] is False
    assert config["gold_dir"] == gold.name
    summary = json.loads(summary_raw)
    assert "hit@1_mean" in summary
    assert total_cases == 1


def test_runner_save_failure_still_exits_zero(tmp_path, monkeypatch):
    from eval.runner import main_async

    asyncio.run(init_db())
    gold = _write_gold(tmp_path)
    _patch_retriever(monkeypatch)
    # The save hook imports resolve_db_path from eval.db_cases at call time.
    monkeypatch.setattr(
        "eval.db_cases.resolve_db_path", lambda: tmp_path / "no" / "dir.db"
    )

    exit_code = asyncio.run(main_async([
        "--no-llm", "--no-db", "--save",
        "--gold-dir", str(gold), "--user-id", "1",
    ]))
    assert exit_code == 0

    conn = sqlite3.connect(str(_db_path()))
    count = conn.execute("SELECT COUNT(*) FROM eval_runs").fetchone()[0]
    conn.close()
    assert count == 0
