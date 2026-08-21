"""Document-related Pydantic models."""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List


class DocumentBase(BaseModel):
    """Base document model."""
    title: Optional[str] = None


class DocumentResponse(DocumentBase):
    """Document response model."""
    id: str
    original_filename: str
    file_type: str
    created_at: datetime
    # Processing state machine checkpoint (services/doc_status.py): one of
    # pending / document_created / indexed / graphed / ready / failed. Lets the
    # UI show a stuck or failed document without polling the SSE history.
    # Defaults to 'pending' for payloads that predate the field.
    status: str = "pending"
    error_message: Optional[str] = None
    # Tags are surfaced on every document-list response so the UI doesn't
    # have to round-trip per card. Always present (possibly empty) — never
    # null — so the client can iterate without null-checks.
    tags: List[str] = Field(default_factory=list)

    class Config:
        from_attributes = True


class TagCreate(BaseModel):
    """Body for POST /api/documents/{id}/tags."""
    tag: str = Field(..., min_length=1, max_length=64)


class TagResponse(BaseModel):
    """A single tag plus how many documents the user has tagged with it."""
    tag: str
    count: int


