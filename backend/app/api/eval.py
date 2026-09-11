"""Eval case management API (FEAT-018): /api/eval/cases.

Turns user feedback into live evaluation cases for the RAG regression gate
(GUIDE-002): a 👎-rated assistant message converts into an ``eval_cases`` row
whose ``expected_chunk_ids`` are the sources the answer cited. The runner
(eval/db_cases.py) merges these rows with the static gold JSON set, so real
user pain feeds the same gate every prompt change must pass.

Idempotency of conversion is carried by the PARTIAL unique index
``uq_eval_cases_source`` (see database.py): the loser of a concurrent double
submit gets IntegrityError and reads back the winner's row — never "check
then insert".

Multi-tenant: every read/write filters ``user_id``; a foreign case is
indistinguishable from a missing one (404, existence not leaked).
"""
import json
import logging
import sqlite3
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from app.api.auth import get_current_user
from app.api.chat import _verify_message_owner
from app.database import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/eval", tags=["eval"])

# When a case is converted from a message, this tag records the provenance so
# reports (and the runner's skipped counters) can tell feedback-derived cases
# from hand-written gold ones.
FEEDBACK_TAG = "user-feedback"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class EvalCaseCreate(BaseModel):
    """Body for POST /api/eval/cases (hand-written case)."""

    query: str = Field(..., min_length=1, max_length=2000)
    expected_chunk_ids: List[str] = Field(default_factory=list)
    expected_keywords: List[str] = Field(default_factory=list)
    expected_answer: str = Field(default="", max_length=4000)
    difficulty: str = Field(default="", max_length=32)
    tags: List[str] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, value: str) -> str:
        # min_length alone accepts "   " — a whitespace-only query would
        # produce a gold case that matches nothing and drags every metric.
        if not value.strip():
            raise ValueError("query must contain non-whitespace characters")
        return value


class EvalCaseFromMessage(BaseModel):
    """Body for POST /api/eval/cases/from-message (feedback conversion).

    ``message_id`` is the ASSISTANT message being rated. ``expected_chunk_ids``
    are not caller-supplied — they come from that message's message_sources
    rows, which is the whole point of the conversion.
    """

    message_id: int = Field(..., ge=1)
    expected_keywords: List[str] = Field(default_factory=list)
    expected_answer: str = Field(default="", max_length=4000)
    difficulty: str = Field(default="", max_length=32)
    tags: List[str] = Field(default_factory=list)


class EvalCaseUpdate(BaseModel):
    """PATCH body — every field optional; omitted keys stay untouched."""

    query: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    expected_chunk_ids: Optional[List[str]] = None
    expected_keywords: Optional[List[str]] = None
    expected_answer: Optional[str] = Field(default=None, max_length=4000)
    difficulty: Optional[str] = Field(default=None, max_length=32)
    tags: Optional[List[str]] = None
    enabled: Optional[bool] = None


class EvalCaseResponse(BaseModel):
    id: int
    user_id: int
    source_message_id: Optional[int] = None
    query: str
    expected_chunk_ids: List[str] = []
    expected_keywords: List[str] = []
    expected_answer: str = ""
    difficulty: str = ""
    tags: List[str] = []
    enabled: bool = True
    created_at: Optional[str] = None


class EvalCaseConvertResponse(EvalCaseResponse):
    created: bool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_case(row) -> dict:
    """Parse a raw eval_cases row (JSON TEXT columns) into an API dict."""
    data = dict(row)
    for key in ("expected_chunk_ids", "expected_keywords", "tags"):
        try:
            data[key] = json.loads(data.get(key) or "[]")
        except (ValueError, TypeError):
            data[key] = []
    data["enabled"] = bool(data.get("enabled", 1))
    return data


async def _get_case_row(db, case_id: int, user_id: int):
    async with db.execute(
        "SELECT * FROM eval_cases WHERE id = ? AND user_id = ?",
        (case_id, user_id),
    ) as cursor:
        return await cursor.fetchone()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/cases", response_model=List[EvalCaseResponse])
async def list_cases(
    current_user: dict = Depends(get_current_user),
    enabled: Optional[bool] = Query(default=None),
):
    """List the current user's eval cases (oldest first)."""
    user_id = current_user["id"]
    sql = "SELECT * FROM eval_cases WHERE user_id = ?"
    params: list = [user_id]
    if enabled is not None:
        sql += " AND enabled = ?"
        params.append(1 if enabled else 0)
    sql += " ORDER BY id ASC"

    async with get_db() as db:
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
    return [_row_to_case(row) for row in rows]


@router.post("/cases", status_code=status.HTTP_201_CREATED, response_model=EvalCaseResponse)
async def create_case(
    body: EvalCaseCreate,
    current_user: dict = Depends(get_current_user),
):
    """Create a hand-written eval case."""
    user_id = current_user["id"]
    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO eval_cases
               (user_id, query, expected_chunk_ids, expected_keywords,
                expected_answer, difficulty, tags, enabled)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                body.query,
                json.dumps(body.expected_chunk_ids),
                json.dumps(body.expected_keywords),
                body.expected_answer,
                body.difficulty,
                json.dumps(body.tags),
                1 if body.enabled else 0,
            ),
        )
        case_id = cursor.lastrowid
        await db.commit()
        row = await _get_case_row(db, case_id, user_id)
    return _row_to_case(row)


@router.post("/cases/from-message", response_model=EvalCaseConvertResponse)
async def convert_from_message(
    body: EvalCaseFromMessage,
    current_user: dict = Depends(get_current_user),
):
    """Convert a rated assistant message into an eval case (FEAT-018).

    query = the user turn that preceded the message; expected_chunk_ids =
    that message's message_sources ordered by rank (NULLs last — rank is
    best-effort indexing from the citation builder). Idempotent: converting
    the same message twice returns the existing case with ``created: false``.
    """
    user_id = current_user["id"]

    async with get_db() as db:
        conversation_id = await _verify_message_owner(db, body.message_id, user_id)
        if conversation_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Message not found"
            )

        async with db.execute(
            "SELECT content FROM messages WHERE conversation_id = ? "
            "AND role = 'user' AND id < ? ORDER BY id DESC LIMIT 1",
            (conversation_id, body.message_id),
        ) as cursor:
            query_row = await cursor.fetchone()
        if query_row is None or not (query_row["content"] or "").strip():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Message has no preceding user turn to use as a query",
            )

        async with db.execute(
            "SELECT chunk_id FROM message_sources WHERE message_id = ? "
            "ORDER BY rank IS NULL, rank",
            (body.message_id,),
        ) as cursor:
            chunk_ids = [r["chunk_id"] for r in await cursor.fetchall()]

        tags = list(body.tags)
        if FEEDBACK_TAG not in tags:
            tags.append(FEEDBACK_TAG)

        try:
            cursor = await db.execute(
                """INSERT INTO eval_cases
                   (user_id, source_message_id, query, expected_chunk_ids,
                    expected_keywords, expected_answer, difficulty, tags)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    user_id,
                    body.message_id,
                    query_row["content"],
                    json.dumps(chunk_ids),
                    json.dumps(body.expected_keywords),
                    body.expected_answer,
                    body.difficulty,
                    json.dumps(tags),
                ),
            )
            case_id = cursor.lastrowid
            created = True
        except sqlite3.IntegrityError:
            # Concurrent double-submit (or a repeat click): the partial
            # unique index already has a row for this message — return it.
            created = False
        await db.commit()

        if not created:
            async with db.execute(
                "SELECT * FROM eval_cases WHERE source_message_id = ? AND user_id = ?",
                (body.message_id, user_id),
            ) as cursor:
                row = await cursor.fetchone()
        else:
            row = await _get_case_row(db, case_id, user_id)

    return {**_row_to_case(row), "created": created}


@router.patch("/cases/{case_id}", response_model=EvalCaseResponse)
async def update_case(
    case_id: int,
    body: EvalCaseUpdate,
    current_user: dict = Depends(get_current_user),
):
    """Partially update a case; omitted fields stay untouched."""
    user_id = current_user["id"]
    sets: List[str] = ["updated_at = CURRENT_TIMESTAMP"]
    params: list = []
    for field, value in (
        ("query", body.query),
        ("expected_answer", body.expected_answer),
        ("difficulty", body.difficulty),
    ):
        if value is not None:
            sets.append(f"{field} = ?")
            params.append(value)
    for field, value in (
        ("expected_chunk_ids", body.expected_chunk_ids),
        ("expected_keywords", body.expected_keywords),
        ("tags", body.tags),
    ):
        if value is not None:
            sets.append(f"{field} = ?")
            params.append(json.dumps(value))
    if body.enabled is not None:
        sets.append("enabled = ?")
        params.append(1 if body.enabled else 0)

    async with get_db() as db:
        row = await _get_case_row(db, case_id, user_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Eval case not found"
            )
        await db.execute(
            f"UPDATE eval_cases SET {', '.join(sets)} WHERE id = ? AND user_id = ?",
            (*params, case_id, user_id),
        )
        await db.commit()
        row = await _get_case_row(db, case_id, user_id)
    return _row_to_case(row)


@router.delete("/cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_case(
    case_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Delete a case. 404 when missing or owned by another user."""
    user_id = current_user["id"]
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM eval_cases WHERE id = ? AND user_id = ?",
            (case_id, user_id),
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Eval case not found"
            )
    return None
