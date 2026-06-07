"""Graph-related Pydantic models."""
from pydantic import BaseModel, Field
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
    """Relation response model."""
    id: Optional[str] = None


class GraphNode(BaseModel):
    """Node for visualization.

    `entity_type` carries the LLM-extracted entity type (PERSON, ORGANIZATION,
    LOCATION, …). `is_center` / `is_highlighted` power the search-mode
    dim/highlight behaviour in the frontend — the full-graph endpoint sets
    both to False; the search endpoint marks center vs. related nodes.
    """
    id: str
    type: str  # "Entity", "Concept", "Chunk" — node *kind*
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
    """
    entity_type: Optional[str] = None
    description: Optional[str] = None


class MergeEntityRequest(BaseModel):
    """Body for merging two entities. Source disappears; target absorbs it.

    The source entity is deleted and all references (MENTIONS, RELATES_TO)
    are re-pointed to the target. If a reference already exists at the
    target with the same relation_type, the duplicate is dropped.
    """
    source: str = Field(..., min_length=1, max_length=200)
    target: str = Field(..., min_length=1, max_length=200)
