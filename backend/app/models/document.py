"""Document-related Pydantic models."""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Dict, Any


class DocumentBase(BaseModel):
    """Base document model."""
    title: Optional[str] = None


class DocumentCreate(DocumentBase):
    """Document creation model."""
    pass


class DocumentInDB(DocumentBase):
    """Document model as stored in database."""
    id: str
    user_id: int
    file_path: str
    original_filename: str
    file_type: str
    created_at: datetime

    class Config:
        from_attributes = True


class DocumentResponse(DocumentBase):
    """Document response model."""
    id: str
    original_filename: str
    file_type: str
    created_at: datetime
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


class ChunkHierarchy(BaseModel):
    """Chunk hierarchy information."""
    level: int
    path: List[str]
    parent_id: Optional[str] = None


class ChunkPosition(BaseModel):
    """Chunk position information."""
    start_line: int
    end_line: int
    prev_chunk_id: Optional[str] = None
    next_chunk_id: Optional[str] = None


class Chunk(BaseModel):
    """Chunk data model."""
    chunk_id: str
    document_id: str
    user_id: int
    content: str
    hierarchy: ChunkHierarchy
    position: ChunkPosition
    metadata: Dict[str, Any] = {}


class ChunkResponse(Chunk):
    """Chunk response model."""
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
