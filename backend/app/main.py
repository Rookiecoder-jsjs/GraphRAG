"""FastAPI main application entry point."""
import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.database import get_db, init_db
from app.logger import configure_logging
from app.middleware import RequestBodyLimitMiddleware, RequestIDMiddleware
from app.services.neo4j_client import get_neo4j_client
from app.services.chroma_client import get_chroma_client
from app.api import auth, documents, search, graph, chat, progress, tags, timeline, dashboard

logger = logging.getLogger(__name__)

# Strong references to startup background tasks. asyncio keeps only weak
# references to tasks; without this, a task can be garbage-collected (and
# silently cancelled) at its first await — prewarm/reconcile would never run
# and nothing would log it.
_background_tasks: list[asyncio.Task] = []


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

    # Prewarm per-user BM25 indexes in the background so the first query
    # doesn't pay a full-scan index build on the request path. Non-blocking:
    # a failure here only logs, never prevents startup.
    if settings.BM25_PREWARM:
        from app.services.bm25 import prewarm_all_bm25
        _background_tasks.append(asyncio.create_task(prewarm_all_bm25()))

    # Reconcile documents abandoned in non-terminal states (e.g. the
    # background pipeline crashed mid-flight). Non-blocking startup sweep.
    from app.services.reconcile import reconcile_stuck_documents
    _background_tasks.append(asyncio.create_task(reconcile_stuck_documents()))

    logger.info("Knowledge Graph System Started")
    yield

    neo4j = await get_neo4j_client()
    await neo4j.close()

    chroma = get_chroma_client()
    chroma.close()

    # Close lazily-initialized LLM/rerank HTTP clients (no-op if never used).
    from app.services.llm import close_llm_service
    from app.services.reranker import close_rerank_service
    from app.services.embedding import close_embedding_service
    await close_llm_service()
    await close_rerank_service()
    await close_embedding_service()

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
    # Global request body size backstop (upload endpoint enforces its own
    # MAX_FILE_SIZE during streaming; this catches every other endpoint).
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.MAX_REQUEST_BODY)
    # Per-request correlation id (X-Request-ID header), surfaced in logs.
    app.add_middleware(RequestIDMiddleware)

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

    @app.get("/")
    async def root():
        return {
            "message": "Knowledge Graph System API",
            "version": "1.0.0",
            "docs": "/docs"
        }

    @app.get("/health")
    async def health():
        """Liveness: the process is up."""
        return {"status": "healthy"}

    @app.get("/health/ready")
    async def health_ready():
        """Readiness: ping every backing store. 503 if any core store is down."""
        checks: dict = {}
        # SQLite
        try:
            async with get_db() as db:
                async with db.execute("SELECT 1") as cur:
                    await cur.fetchone()
            checks["sqlite"] = "ok"
        except Exception as e:
            checks["sqlite"] = f"fail: {type(e).__name__}"
        # ChromaDB (heartbeat is a synchronous HTTP round-trip; run it off
        # the event loop so a slow/unreachable server can't stall readiness
        # requests for other healthy checks).
        try:
            await asyncio.to_thread(get_chroma_client().heartbeat)
            checks["chroma"] = "ok"
        except Exception as e:
            checks["chroma"] = f"fail: {type(e).__name__}"
        # Neo4j
        try:
            neo4j = await get_neo4j_client()
            async with neo4j.session() as s:
                await s.run("RETURN 1")
            checks["neo4j"] = "ok"
        except Exception as e:
            checks["neo4j"] = f"fail: {type(e).__name__}"
        # BM25 prewarm status (informational, not gating).
        from app.services.bm25 import get_prewarm_state
        checks["bm25_prewarm"] = get_prewarm_state()
        core = ("sqlite", "chroma", "neo4j")
        ready = all(checks.get(k) == "ok" for k in core)
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"status": "ready" if ready else "degraded", "checks": checks},
        )

    return app


app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8001, reload=True)
