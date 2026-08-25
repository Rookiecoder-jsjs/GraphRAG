"""NC knowledge-base MCP server (GUIDE-003 T2-3).

Exposes the system's read-only retrieval/graph capabilities as standard MCP
tools so any MCP client (Claude Desktop, Claude Code, codex) can query the
knowledge base with zero frontend investment — the codex "capability-as-tool"
pattern from mcp-server/ + connectors.

Run mode: stdio JSON-RPC over stdin/stdout, launched by the parent MCP client:

    KG_MCP_TOKEN=<token> python mcp_server/server.py

Design constraints:
  * Read-only — no tool writes to SQLite / Chroma / Neo4j. The four tools
    map onto existing read paths only (retriever.retrieve,
    neo4j.search_entities / get_related_entities / get_entity_detail,
    documents table).
  * Reuses the root .venv and the same .env-backed settings as the FastAPI
    app; both processes may run concurrently (SQLite WAL allows a concurrent
    reader).
  * user_id scoping: every tool takes an explicit ``user_id`` argument. The
    MCP layer has no JWT session, so the caller states whose knowledge base
    it is reading; token possession is the access control.
"""
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make `app.*` importable when launched as a script from backend/.
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Anchor relative data paths to backend/ BEFORE importing app.config. The
# FastAPI app always runs with CWD=backend so its "./data/..." defaults
# resolve there; an MCP client spawns this process with an arbitrary CWD
# (its own project dir), which would silently create an empty SQLite file
# in the wrong place ("no such table: documents"). Same convention as the
# launcher scripts: the data root follows the app package, not the CWD.
import os  # noqa: E402

os.chdir(_BACKEND_ROOT)

# Pre-import every app service module the tools touch BEFORE the stdio
# (anyio) event loop starts. First-import of some packages (notably neo4j)
# deadlocks when it happens inside a running anyio loop — observed as tools
# hanging forever on their first Neo4j call while the same import in a plain
# asyncio process completes in <1s. Importing eagerly at module scope, where
# no loop is running yet, avoids that entirely and also moves any import
# error to startup instead of mid-request.
import app.services.neo4j_client  # noqa: F401,E402
import app.services.retriever  # noqa: F401,E402
import app.database  # noqa: F401,E402

from mcp.server.fastmcp import FastMCP  # noqa: E402

from mcp_server import auth  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    # stderr ONLY — stdout is the stdio JSON-RPC channel; anything printed
    # there corrupts the protocol stream.
    stream=sys.stderr,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("mcp_server")

mcp = FastMCP(
    "nc-knowledge-base",
    instructions=(
        "Query a personal knowledge graph / RAG knowledge base. "
        "search_knowledge answers questions from indexed documents; "
        "search_graph and get_entity_detail explore the entity graph; "
        "list_documents shows what is indexed. All reads are scoped to one "
        "user_id."
    ),
)


# ---------------------------------------------------------------------------
# Tool 1: semantic search over indexed chunks (hybrid BM25+vector+rerank)
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_knowledge(
    query: str,
    user_id: int,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """Search the knowledge base for chunks relevant to ``query``.

    Runs the same hybrid pipeline the chat product uses (BM25 + vector +
    RRF fusion + cross-encoder rerank). Returns up to ``top_k`` chunks with
    content previews and source-document titles.
    """
    top_k = max(1, min(int(top_k), 20))
    if not query.strip():
        return []

    from app.services.retriever import retrieve

    result = await retrieve(
        query.strip(), int(user_id), top_k=top_k,
        use_graph_rag=False, enable_rewrite=False,
    )
    chunks = result.get("chunks", [])[:top_k]
    titles = await _doc_titles_for_chunks(chunks, int(user_id))

    return [
        {
            "chunk_id": c.get("chunk_id") or c.get("id"),
            "document_id": (c.get("metadata") or {}).get("document_id")
            or c.get("document_id"),
            "title": titles.get(
                (c.get("metadata") or {}).get("document_id")
                or c.get("document_id"),
                "Untitled",
            ),
            "content": (c.get("content") or "")[:2000],
            "score": c.get("relevance_score"),
        }
        for c in chunks
    ]


async def _doc_titles_for_chunks(chunks, user_id) -> Dict[str, str]:
    """Batch-resolve document titles for retrieved chunks (single IN query)."""
    doc_ids = list({
        (c.get("metadata") or {}).get("document_id") or c.get("document_id")
        for c in chunks
    } - {None})
    if not doc_ids:
        return {}
    from app.database import get_db

    placeholders = ",".join("?" * len(doc_ids))
    async with get_db() as db:
        async with db.execute(
            f"SELECT id, COALESCE(NULLIF(title, ''), original_filename, 'Untitled') "
            f"AS title FROM documents WHERE id IN ({placeholders}) AND user_id = ?",
            (*doc_ids, user_id),
        ) as cursor:
            rows = await cursor.fetchall()
    return {r["id"]: r["title"] for r in rows}


# ---------------------------------------------------------------------------
# Tool 2: entity-name search in the knowledge graph
# ---------------------------------------------------------------------------

@mcp.tool()
async def search_graph(
    entity_or_query: str,
    user_id: int,
    depth: int = 1,
) -> Dict[str, Any]:
    """Look up entities by name and expand their graph neighborhood.

    Finds entities whose name contains ``entity_or_query`` (case-insensitive)
    and returns their related entities within ``depth`` hops (1-3).
    """
    depth = max(1, min(int(depth), 3))
    needle = entity_or_query.strip()
    if not needle:
        return {"entities": [], "related": [], "relations": []}

    neo4j = await _neo4j()
    matches = await neo4j.search_entities(needle, int(user_id), limit=10)
    if not matches:
        return {"entities": [], "related": [], "relations": []}

    names = [e["name"] for e in matches]
    graph_view = await neo4j.get_related_entities(names, int(user_id), depth=depth)
    return {
        "entities": matches,
        # get_related_entities returns center_nodes/related_nodes/relations.
        "center_nodes": graph_view.get("center_nodes", []),
        "related_nodes": graph_view.get("related_nodes", []),
        "relations": graph_view.get("relations", []),
    }


async def _neo4j():
    from app.services.neo4j_client import get_neo4j_client

    client = await get_neo4j_client()
    # The FastAPI lifespan normally owns connect(); this process is standalone.
    await client.connect()
    return client


# ---------------------------------------------------------------------------
# Tool 3: full entity detail envelope
# ---------------------------------------------------------------------------

@mcp.tool()
async def get_entity_detail(name: str, user_id: int) -> Optional[Dict[str, Any]]:
    """Full detail for one entity: stats, mentioning documents, related
    entities, and sample chunk previews. Returns null when unknown."""
    clean = name.strip()
    if not clean:
        return None
    neo4j = await _neo4j()
    envelope = await neo4j.get_entity_detail(clean, int(user_id))
    if envelope is None:
        return None

    # Hydrate first_seen from SQLite (same convention as api/graph.py).
    doc_ids = [d.get("doc_id") for d in envelope.get("documents", [])]
    if doc_ids:
        from app.database import get_db

        placeholders = ",".join("?" * len(doc_ids))
        async with get_db() as db:
            async with db.execute(
                f"SELECT id, created_at FROM documents "
                f"WHERE id IN ({placeholders}) AND user_id = ?",
                (*doc_ids, int(user_id)),
            ) as cursor:
                rows = await cursor.fetchall()
        created_at_by_id = {r["id"]: r["created_at"] for r in rows}
        for d in envelope["documents"]:
            d["first_seen"] = created_at_by_id.get(d.get("doc_id"))
    return envelope


# ---------------------------------------------------------------------------
# Tool 4: document inventory
# ---------------------------------------------------------------------------

@mcp.tool()
async def list_documents(user_id: int) -> List[Dict[str, Any]]:
    """List the user's indexed documents with status and timestamps."""
    from app.database import get_db

    async with get_db() as db:
        async with db.execute(
            "SELECT id, title, original_filename, file_type, status, "
            "created_at, updated_at FROM documents "
            "WHERE user_id = ? ORDER BY created_at DESC LIMIT 100",
            (int(user_id),),
        ) as cursor:
            rows = await cursor.fetchall()
    return [
        {
            "id": r["id"],
            "title": r["title"] or r["original_filename"] or "Untitled",
            "file_type": r["file_type"],
            "status": r["status"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]


def main() -> None:
    """Startup gate + stdio loop. Called only under ``python server.py``."""
    auth.require_token()  # fail fast before any transport is opened
    logger.info("nc-knowledge-base MCP server starting (stdio)")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
