"""Eval run history endpoints (FEAT-021): GET /api/eval/runs.

Own router module, same ``/api/eval`` prefix as eval.py (no overlapping
paths — eval.py only exposes /cases*), mirroring the url_ingest/documents
co-prefix precedent. Read-only list: runs are append-only evidence behind
军规②, so there is deliberately no detail/update/delete surface. The rows
are written by ``eval.runner --save`` (eval/db_runs.py), never by this API.
"""
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.database import get_db

router = APIRouter(prefix="/api/eval", tags=["eval"])


class EvalRunResponse(BaseModel):
    id: int
    label: str = ""
    mode: str = ""
    config: Dict[str, Any] = {}
    summary: Dict[str, Any] = {}
    total_cases: int = 0
    created_at: Optional[str] = None


def _row_to_run(row) -> dict:
    """Parse a raw eval_runs row (JSON TEXT columns) into an API dict."""
    data = dict(row)
    for key in ("config", "summary"):
        try:
            parsed = json.loads(data.get(key) or "{}")
        except (ValueError, TypeError):
            parsed = {}
        # A non-dict payload (corrupt row) must not break the list.
        data[key] = parsed if isinstance(parsed, dict) else {}
    data["total_cases"] = int(data.get("total_cases") or 0)
    return data


@router.get("/runs", response_model=List[EvalRunResponse])
async def list_runs(
    current_user: dict = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=200),
):
    """List the current user's eval runs, newest first."""
    user_id = current_user["id"]
    async with get_db() as db:
        async with db.execute(
            "SELECT id, user_id, label, mode, config, summary, total_cases, "
            "created_at FROM eval_runs WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
    return [_row_to_run(row) for row in rows]
