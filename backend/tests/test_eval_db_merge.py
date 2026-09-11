"""Tests for eval/db_cases.py — merging SQLite eval_cases into the gold set
(FEAT-018). Pure-file behavior must be untouched when the table is empty or
missing; on duplicate (normalized) queries the DB case wins and the dropped
file case is reported for the run summary.
"""
import asyncio
import sqlite3

from eval.db_cases import load_db_cases, merge_cases
from eval.runner import load_gold_set


def _write_gold(gold_dir, case_id, query, chunk_ids):
    gold_dir.mkdir(parents=True, exist_ok=True)
    path = gold_dir / f"{case_id}.json"
    path.write_text(
        f'{{"id": "{case_id}", "query": "{query}", '
        f'"expected_chunk_ids": {chunk_ids}, "expected_keywords": []}}',
        encoding="utf-8",
    )


def _make_db(db_path, rows):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE eval_cases (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               user_id INTEGER NOT NULL,
               source_message_id INTEGER,
               query TEXT NOT NULL,
               expected_chunk_ids TEXT NOT NULL DEFAULT '[]',
               expected_keywords TEXT NOT NULL DEFAULT '[]',
               expected_answer TEXT NOT NULL DEFAULT '',
               difficulty TEXT NOT NULL DEFAULT '',
               tags TEXT NOT NULL DEFAULT '[]',
               enabled INTEGER NOT NULL DEFAULT 1,
               created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
               updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
           )"""
    )
    conn.executemany(
        "INSERT INTO eval_cases (user_id, query, expected_chunk_ids, tags, enabled) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def test_load_db_cases_and_merge_db_wins_on_duplicate_query(tmp_path):
    gold_dir = tmp_path / "gold"
    _write_gold(gold_dir, "01_dup", "重复问题", '["g1"]')
    _write_gold(gold_dir, "02_unique", "独立问题", '["g2"]')

    db = tmp_path / "app.db"
    _make_db(db, [
        # enabled case whose normalized query duplicates the file case.
        (1, " 重复问题 ", '["d1"]', '["user-feedback"]', 1),
        # disabled rows must NOT load.
        (1, "disabled-case", '["d2"]', "[]", 0),
        # other user's cases must NOT load.
        (2, "foreign-case", '["d3"]', "[]", 1),
    ])

    file_cases = load_gold_set(gold_dir)
    assert [c.id for c in file_cases] == ["01_dup", "02_unique"]

    db_cases = load_db_cases(db, 1)
    assert [c.id for c in db_cases] == ["db-1"]
    assert db_cases[0].expected_chunk_ids == ["d1"]
    assert "user-feedback" in db_cases[0].tags
    assert db_cases[0].metadata["source"] == "eval_cases"

    merged, skipped = merge_cases(file_cases, db_cases)
    # The DB (latest user intent) wins the duplicate; the dropped file case
    # is surfaced so the report can show it.
    assert skipped == ["01_dup"]
    assert [c.id for c in merged] == ["02_unique", "db-1"]
    # Normalization is whitespace/case-insensitive: " 重复问题 " matched.
    assert merged[1].query == " 重复问题 "


def test_load_db_cases_missing_table_returns_empty(tmp_path):
    # A database never bootstrapped by the app must not crash the runner —
    # eval stays pure-file (external contract preserved).
    assert load_db_cases(tmp_path / "missing.db", 1) == []


def test_merge_preserves_all_file_cases_when_db_empty(tmp_path):
    gold_dir = tmp_path / "gold"
    _write_gold(gold_dir, "01_a", "问题A", '["g1"]')
    file_cases = load_gold_set(gold_dir)
    merged, skipped = merge_cases(file_cases, [])
    assert merged == file_cases and skipped == []
