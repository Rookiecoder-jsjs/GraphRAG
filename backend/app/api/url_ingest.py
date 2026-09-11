"""URL ingestion endpoint (FEAT-019): POST /api/documents/ingest-url.

Lives on its own router module (same ``/api/documents`` prefix as
documents.py — no overlapping paths, verified) so documents.py, already the
largest API module, does not grow further. The fetch and its SSRF guards live
in services/url_fetcher.py; this module only maps fetch outcomes to HTTP
codes and hands the result to the SAME background pipeline a file upload
uses — ingest-gate queueing, SSE progress, and failure cleanup apply
unchanged.

Stored shape differences vs. a file upload:
  * HTML sources: markdown is produced in-place; ``file_path`` is NULL and
    ``file_type`` is ``html`` (the raw HTML is deliberately never persisted,
    so these documents cannot be reprocessed — reprocess answers 409).
  * PDF / text sources: bytes land in UPLOAD_DIR like an upload, so the
    regular reprocess path works for them.
"""
import asyncio
import logging
import os
import uuid
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.api.documents import process_document_background
from app.auth.rate_limit import SlidingWindowLimiter, enforce_rate_limit
from app.config import get_settings
from app.database import get_db
from app.models.document import DocumentResponse
from app.services.doc_status import DocStatus
from app.services.url_fetcher import (
    UrlFetchFailed,
    UrlNotAllowed,
    UnsupportedContentType,
    fetch_url_document,
)
from app.utils.md_parser import (
    clean_markdown,
    convert_document_to_markdown,
    extract_title_from_markdown,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/documents", tags=["documents"])

# Each ingest spawns the full billable pipeline (embedding + LLM extraction).
# Tighter than chat's 20/min: a scripted loop of URL ingests burns provider
# quota far faster than a chat loop does.
url_ingest_limiter = SlidingWindowLimiter(max_calls=10, window_seconds=60)


class UrlIngestRequest(BaseModel):
    """Body for POST /api/documents/ingest-url."""

    url: str = Field(
        ...,
        min_length=8,
        max_length=2048,
        description="Absolute http(s) URL of the page or document to ingest",
    )


def _remove_quietly(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


@router.post(
    "/ingest-url",
    status_code=status.HTTP_201_CREATED,
    response_model=DocumentResponse,
)
async def ingest_url(
    body: UrlIngestRequest,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user),
):
    """Fetch a public URL and ingest it through the upload pipeline.

    400 for an invalid/blocked (SSRF) URL, 502 for fetch failures, 415 for
    content types we cannot ingest, 422 when nothing readable survives
    extraction. On success the document row is created in ``pending`` and the
    standard background pipeline is dispatched.
    """
    settings = get_settings()
    user_id = current_user["id"]
    enforce_rate_limit(url_ingest_limiter, f"url-ingest:{user_id}")

    try:
        fetched = await fetch_url_document(body.url)
    except UrlNotAllowed as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e) or "URL not allowed",
        )
    except UrlFetchFailed as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)
        )
    except UnsupportedContentType as e:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(e)
        )

    doc_id = str(uuid.uuid4())
    host = urlparse(fetched.final_url).hostname or "url"
    original_filename = body.url[:512]
    title = fetched.title

    if fetched.kind == "html":
        file_path = None
        file_type = "html"
        markdown_content = clean_markdown(fetched.markdown or "")
    else:
        # PDF / text sources persist their bytes so the regular reprocess
        # path can re-convert them later, exactly like an uploaded file.
        ext = fetched.kind  # "pdf" | "txt" | "md"
        file_type = ext
        file_path = os.path.join(settings.UPLOAD_DIR, f"{doc_id}.{ext}")
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(fetched.data or b"")
        try:
            converted, converted_title = await asyncio.to_thread(
                convert_document_to_markdown, file_path, ext
            )
            markdown_content = clean_markdown(converted)
        except Exception as e:
            logger.error(
                "URL ingest conversion failed for %s: %s", doc_id, e, exc_info=True
            )
            _remove_quietly(file_path)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to convert fetched document",
            )
        title = fetched.title or converted_title

    if not title:
        title = extract_title_from_markdown(markdown_content)
    if not title:
        title = host
    if not markdown_content.strip():
        _remove_quietly(file_path)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Could not extract readable content from the URL",
        )

    async with get_db() as db:
        try:
            await db.execute(
                """INSERT INTO documents
                   (id, user_id, title, file_path, original_filename, file_type, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, user_id, title, file_path, original_filename, file_type,
                 DocStatus.PENDING.value),
            )
            await db.commit()
        except Exception as e:
            # Mirror upload's contract: a failed row write must not leave an
            # orphan blob on disk.
            logger.error(
                "Failed to insert URL-ingested document row %s: %s", doc_id, e,
                exc_info=True,
            )
            _remove_quietly(file_path)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save document",
            )
        async with db.execute(
            "SELECT id, title, original_filename, file_type, created_at, status, "
            "error_message FROM documents WHERE id = ?",
            (doc_id,),
        ) as cursor:
            doc = await cursor.fetchone()

    background_tasks.add_task(
        process_document_background, doc_id, user_id, markdown_content, title
    )
    return dict(doc)
