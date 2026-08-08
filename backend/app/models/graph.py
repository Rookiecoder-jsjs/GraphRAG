"""Graph-related Pydantic models."""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime


class EntityType(str):
    """Entity types."""
    PERSON = "PERSON"
    ORGANIZATION = "ORGANIZATION"
    LOCATION = "LOCATION"
    TIME = "TIME"
    CONCEPT = "CONCEPT"
    EVENT = "EVENT"
    OTHER = "OTHER"


# Canonical entity types. Mirrors the frontend ENTITY_TYPE_OPTIONS dropdown
# (frontend/src/views/GraphPage.vue) and the EntityType constants above.
# Used to validate *manual* entity edits (PATCH /api/graph/entities/{name});
# the LLM extraction path is intentionally NOT restricted, so existing and
# LLM-produced types are never rejected - only client-driven writes are.
ALLOWED_ENTITY_TYPES = frozenset({
    EntityType.PERSON, EntityType.ORGANIZATION, EntityType.LOCATION,
    EntityType.TIME, EntityType.CONCEPT, EntityType.EVENT, EntityType.OTHER,
})


class Entity(BaseModel):
    """Entity node model."""
    name: str
    type: str
    description: Optional[str] = None
    user_id: Optional[int] = None


class EntityResponse(Entity):
    """Entity response model."""
    id: Optional[str] = None


class Relation(BaseModel):
    """Relationship between entities."""
    source: str
    target: str
    relation_type: str
    properties: Dict[str, Any] = {}


class RelationResponse(Relation):
    """Relationship response model."""
    id: Optional[str] = None


class GraphNode(BaseModel):
    """Node for visualization.

    `entity_type` carries the LLM-extracted entity type (PERSON, ORGANIZATION,
    LOCATION, …). `is_center` / `is_highlighted` power the search-mode
    dim/highlight behaviour in the frontend - the full-graph endpoint sets
    both to False; the search endpoint marks center vs. related nodes.
    """
    id: str
    type: str  # "Entity", "Concept", "Chunk" - node *kind*
    label: str
    properties: Dict[str, Any] = {}
    x: Optional[float] = None
    y: Optional[float] = None
    entity_type: Optional[str] = None  # PERSON / ORG / LOCATION / … (Entity nodes only)
    description: Optional[str] = None  # 实体描述（提升到顶层以便 Edit 面板直接读取）
    is_center: bool = False
    is_highlighted: bool = False


class GraphEdge(BaseModel):
    """Edge for visualization."""
    id: str
    source: str
    target: str
    label: str
    type: str


class GraphVisualization(BaseModel):
    """Graph visualization data."""
    nodes: List[GraphNode]
    edges: List[GraphEdge]


class GraphQuery(BaseModel):
    """Graph query request."""
    query: str = Field(..., description="Search query to find related entities")
    depth: int = Field(default=2, ge=1, le=3, description="Search depth")


class GraphQueryResponse(BaseModel):
    """Graph query response."""
    center_nodes: List[EntityResponse]
    related_nodes: List[EntityResponse]
    relations: List[RelationResponse]
    visualization: GraphVisualization


# ---------- Entity curation (manual cleanup of LLM-extracted data) -------

class UpdateEntityRequest(BaseModel):
    """PATCH body for editing one entity.

    `entity_type` and `description` are independent: any subset may be sent.
    Send `description=""` to clear the description; omit the key to leave it
    untouched. (The router treats absent key as `None`.)

    `entity_type`, when provided, must be one of ALLOWED_ENTITY_TYPES
    (case-insensitive; normalized to upper-case before storage).
    """
    entity_type: Optional[str] = None
    description: Optional[str] = None

    @field_validator("entity_type")
    @classmethod
    def _validate_entity_type(cls, v: Optional[str]) -> Optional[str]:
        """Normalize to upper-case and enforce the ALLOWED_ENTITY_TYPES whitelist.

        `None` (key omitted) is left untouched so callers can PATCH a single
        field. An invalid value raises ValueError -> HTTP 422.
        """
        if v is None:
            return v  # omitted -> leave untouched (PATCH semantics)
        v = v.strip().upper()
        if v not in ALLOWED_ENTITY_TYPES:
            raise ValueError(
                f"entity_type must be one of {sorted(ALLOWED_ENTITY_TYPES)}, got {v!r}"
            )
        return v


class MergeEntityRequest(BaseModel):
    """Body for merging two entities. Source disappears; target absorbs it.

    The source entity is deleted and all references (MENTIONS, RELATES_TO)
    are re-pointed to the target. If a reference already exists at the
    target with the same relation_type, the duplicate is dropped.
    """
    source: str = Field(..., min_length=1, max_length=200)
    target: str = Field(..., min_length=1, max_length=200)
