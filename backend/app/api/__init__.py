"""API routers package."""
from app.api import auth
from app.api import documents
from app.api import search
from app.api import graph
from app.api import chat
from app.api import tags
from app.api import timeline
from app.api import dashboard

__all__ = ["auth", "documents", "search", "graph", "chat", "tags", "timeline", "dashboard"]
