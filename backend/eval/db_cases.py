"""Load eval cases persisted in SQLite (``eval_cases`` table) for the runner.

FEAT-018 closes the feedback loop: a 👎 message converts into an eval_cases
row (api/eval.py), and this module turns those rows into ``GoldCase`` objects
so ``python -m eval.runner`` evaluates them alongside the static gold JSON
set. Design constraints:

* The runner stays decoupled from app imports — this module uses only the
  stdlib and ``.runner``'s GoldCase (imported lazily inside the function so
  runner → db_cases stays import-cycle-free).
* A database that was never bootstrapped (missing table) yields ``[]`` —
  eval must stay pure-file, preserving the pre-FEAT-018 contract.
* Case ids are prefixed ``db-`` and can therefore never collide with gold
  file ids.
* Merge rule (see :func:`merge_cases`): file cases run first, DB cases after;
  when a normalized query appears in both, the DB case WINS (it reflects the
  user's latest intent) and the dropped file case is returned in ``skipped``
  so the run summary can account for it.
"""
import json
import sqlite3
from pathlib import Path
from typing import List, Tuple


def resolve_db_path() -> Path:
    """Resolve the app's SQLITE_PATH relative to backend/ (the runner may be
    started from another CWD; rebuild_chroma.py anchors the same way)."""
    from app.config import get_settings

    path = Path(get_settings().SQLITE_PATH)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / path
    return path


def load_db_cases(db_path, user_id: int) -> List:
    """Read the user's enabled eval_cases as GoldCase objects (``[]`` when
    the table doesn't exist or the file is unreadable — eval is best-effort
    and must never hard-fail because the DB side of the loop is empty)."""
    from .runner import GoldCase

    try:
        conn = sqlite3.connect(str(db_path))
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            "SELECT id, query, expected_chunk_ids, expected_keywords, "
            "expected_answer, difficulty, tags FROM eval_cases "
            "WHERE user_id = ? AND enabled = 1 ORDER BY id",
            (user_id,),
        ).fetchall()
    except sqlite3.Error:
        # Missing table (fresh DB) or schema drift — pure-file behavior.
        return []
    finally:
        conn.close()

    def _load_list(raw: str) -> list:
        try:
            value = json.loads(raw or "[]")
        except ValueError:
            return []
        return value if isinstance(value, list) else []

    cases = []
    for row in rows:
        cases.append(GoldCase(
            id=f"db-{row[0]}",
            query=row[1],
            expected_chunk_ids=_load_list(row[2]),
            expected_keywords=_load_list(row[3]),
            expected_answer=row[4] or "",
            difficulty=row[5] or "",
            tags=_load_list(row[6]),
            metadata={"source": "eval_cases", "source_row_id": row[0]},
        ))
    return cases


def _normalize_query(query: str) -> str:
    """Whitespace/case-insensitive query identity for duplicate detection."""
    return " ".join((query or "").split()).lower()


def merge_cases(file_cases: List, db_cases: List) -> Tuple[List, List[str]]:
    """Merge gold-file cases with DB cases. Returns ``(merged, skipped)``:
    file cases whose normalized query a DB case covers are dropped (db wins)
    and their ids reported; kept file cases keep their original order, DB
    cases follow in id order."""
    db_queries = {_normalize_case_query(c) for c in db_cases}
    merged = [c for c in file_cases if _normalize_case_query(c) not in db_queries]
    skipped = [c.id for c in file_cases if _normalize_case_query(c) in db_queries]
    return merged + list(db_cases), skipped


def _normalize_case_query(case) -> str:
    return _normalize_query(getattr(case, "query", ""))
