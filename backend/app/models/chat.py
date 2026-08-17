"""Chat-related Pydantic models."""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class ChatMessage(BaseModel):
    """Chat message model."""
    role: str = Field(..., pattern="^(user|assistant|system)$")
    content: str


class ChatRequest(BaseModel):
    """Chat request model."""
    # Upper bound so one oversized message can't be persisted verbatim and
    # shipped whole into the LLM prompt (context overflow -> provider 400).
    message: str = Field(..., min_length=1, max_length=8000)
    conversation_id: Optional[str] = None
    include_context: bool = True
    use_graph_rag: bool = False  # opt-in: graph-first candidate set
    compare_mode: bool = False   # opt-in: structure answer as a comparison
    # opt-in: Qwen hybrid-thinking mode — the model streams its reasoning
    # (forwarded as `event: thinking` SSE frames) before the answer body.
    # Noticeably slower first token, occasionally better answers. Only the
    # streaming endpoint honors it; the non-streaming /chat path ignores it.
    enable_thinking: bool = False


class ChatResponse(BaseModel):
    """Chat response model."""
    message: str
    conversation_id: str
    related_chunks: List[Dict[str, Any]] = []
    related_entities: List[Dict[str, Any]] = []
    # Fraction (0.0–1.0) of source chips that the answer actually
    # cites. A low value (e.g. 0.2) means the LLM mostly hand-waved
    # and the sources weren't really used. Front-end renders this as
    # a small "X% cited" indicator.
    citation_coverage: float = 0.0


class Conversation(BaseModel):
    """Conversation model.

    ``message_count`` / ``last_message`` / ``last_activity`` are enrichment
    fields populated by the list endpoint (subqueries over messages) so the
    history-management UI can show a preview, a message count and a
    last-activity time without N+1 round-trips.
    """
    id: str
    user_id: int
    title: Optional[str] = None
    created_at: datetime
    message_count: int = 0
    last_message: Optional[str] = None
    last_activity: Optional[datetime] = None

    class Config:
        from_attributes = True


class SearchRequest(BaseModel):
    """Search request model."""
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)
    include_context: bool = True


class SearchResponse(BaseModel):
    """Search response model."""
    query: str
    chunks: List[Dict[str, Any]]
    entities: List[Dict[str, Any]]
    relations: List[Dict[str, Any]]
