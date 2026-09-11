"""Persist eval run summaries (FEAT-021).

The eval package stays on the stdlib sqlite3 driver (sync), same as
db_cases.py. Contract: **best-effort** — recording history must never fail
the evaluation itself, so every sqlite error degrades to ``None`` plus a
stderr warning and the runner keeps exit code 0. Table DDL lives in
``app.database.init_db`` (the app creates it at every startup); a standalone
``python -m eval.runner`` against an uninitialized DB simply saves nothing.
"""
import json
import sqlite3
import sys
from typing import Dict, Optional


def save_run(
    db_path,
    user_id: int,
    *,
    label: str,
    mode: str,
    config: Dict,
    summary: Dict,
    total_cases: int,
) -> Optional[int]:
    """Insert one eval_runs row; return the new id, or None on any failure."""
    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error as e:
        print(f"[warn] cannot open eval db for --save: {e}", file=sys.stderr)
        return None
    try:
        cursor = conn.execute(
            """INSERT INTO eval_runs
               (user_id, label, mode, config, summary, total_cases)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                label,
                mode,
                json.dumps(config, ensure_ascii=False),
                json.dumps(summary, ensure_ascii=False),
                int(total_cases),
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.Error as e:
        print(f"[warn] failed to save eval run: {e}", file=sys.stderr)
        return None
    finally:
        conn.close()
