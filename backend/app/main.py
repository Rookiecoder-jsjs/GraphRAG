"""FastAPI main application entry point."""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import init_db
from app.logger import configure_logging
from app.services.neo4j_client import get_neo4j_client
from app.services.chroma_client import get_chroma_client
from app.api import auth, documents, search, graph, chat, progress, tags, timeline, dashboard

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    configure_logging()
    settings = get_settings()

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(settings.SQLITE_PATH), exist_ok=True)

    await init_db()

    neo4j = await get_neo4j_client()
    await neo4j.connect()

    chroma = get_chroma_client()
    chroma.connect()

    logger.info("Knowledge Graph System Started")
    yield

    neo4j = await get_neo4j_client()
    await neo4j.close()

    chroma = get_chroma_client()
    chroma.close()

    logger.info("Knowledge Graph System Stopped")


def create_app() -> FastAPI:
    """Create FastAPI application."""
    app = FastAPI(
        title="Knowledge Graph System API",
        description="Multi-user knowledge graph with Neo4j and ChromaDB",
        version="1.0.0",
        lifespan=lifespan
    )

    # CORS - use environment-configured origins; never use ["*"] with credentials
    settings = get_settings()
    allowed_origins = [
        origin.strip() for origin in settings.CORS_ALLOWED_ORIGINS.split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins or ["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # Include routers
    app.include_router(auth.router)
    app.include_router(documents.router)
    app.include_router(search.router)
    app.include_router(graph.router)
    app.include_router(chat.router)
    app.include_router(progress.router)
    app.include_router(tags.router)
    app.include_router(timeline.router)
    app.include_router(dashboard.router)
    app.include_router(timeline.router)
    app.include_router(tags.router)

    @app.get("/")
    async def root():
        return {
            "message": "Knowledge Graph System API",
            "version": "1.0.0",
            "docs": "/docs"
        }

    @app.get("/health")
    async def health():
        return {"status": "healthy"}

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
