"""Document management API endpoints."""
import logging
import os
import uuid
from pathlib import Path
from typing import List, Optional

import numpy as np
from fastapi import APIRouter, Depends, UploadFile, File, HTTPException, status, BackgroundTasks

from app.config import get_settings
from app.database import get_db
from app.api.auth import get_current_user
from app.models.document import DocumentResponse, ChunkResponse, TagCreate, TagResponse
from app.utils.md_parser import convert_document_to_markdown, clean_markdown, extract_title_from_markdown
from app.services.chunker import chunk_markdown
from app.services.embedding import get_embedding_service, EmbeddingServiceError
from app.services.neo4j_client import get_neo4j_client
from app.services.chroma_client import get_chroma_client
from app.services.bm25 import get_bm25_service
from app.services.entity_extractor import get_entity_extractor
from app.services.progress_tracker import get_progress_emitter

logger = logging.getLogger(__name__)

# Tags are user-typed free text. We normalise on the way in so that "Research",
# "research", "  #research " all collide into the same row — otherwise the
# filter UI would show two pills for what is visually one tag, and the
# UNIQUE(document_id, user_id, tag) constraint would fail on the second add.


def normalize_tag(raw: str) -> Optional[str]:
    """Normalise a user-supplied tag. Returns None if the input is blank
    after stripping whitespace and a leading '#'.

    Rules:
      * strip surrounding whitespace
      * drop one or more leading '#' characters (with their following spaces)
      * lowercase

    The loop handles strings like "# # #" — each leading '#' is peeled off
    one at a time, so a string of nothing-but-#s and whitespace collapses
    to the empty string (which becomes None).
    """
    if not raw:
        return None
    s = raw.strip()
    while s.startswith("#"):
        s = s[1:].lstrip()
    return s.lower() or None


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human readable string."""
    if seconds < 60:
        return f"{seconds:.1f}秒"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = seconds % 60
        return f"{minutes}分{secs:.0f}秒"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}小时{minutes}分"

router = APIRouter(prefix="/api/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.doc', '.txt', '.md', '.markdown'}


def get_file_extension(filename: str) -> str:
    """Get file extension."""
    return Path(filename).suffix.lower()


def is_allowed_file(filename: str) -> bool:
    """Check if file extension is allowed."""
    return get_file_extension(filename) in ALLOWED_EXTENSIONS


@router.post("/upload", response_model=DocumentResponse)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    """Upload and process a document."""
    settings = get_settings()
    user_id = current_user["id"]

    # Validate file
    if not is_allowed_file(file.filename):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"
        )

    # Check file size
    file_content = await file.read()
    if len(file_content) > settings.MAX_FILE_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File too large. Max size: {settings.MAX_FILE_SIZE / 1024 / 1024:.1f}MB"
        )

    # Generate document ID
    doc_id = str(uuid.uuid4())

    # Save file
    file_ext = get_file_extension(file.filename)
    file_name = f"{doc_id}{file_ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, file_name)

    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    with open(file_path, 'wb') as f:
        f.write(file_content)

    # Convert to markdown
    try:
        markdown_content, extracted_title = convert_document_to_markdown(file_path, file_ext[1:])
        markdown_content = clean_markdown(markdown_content)
    except Exception as e:
        # Clean up file on error
        os.remove(file_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to convert document: {str(e)}"
        )

    if not markdown_content.strip():
        os.remove(file_path)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not extract text from document"
        )

    # Extract title
    title = extracted_title or extract_title_from_markdown(markdown_content) or file.filename

    # Save to database
    async with get_db() as db:
        await db.execute(
            """INSERT INTO documents
               (id, user_id, title, file_path, original_filename, file_type)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (doc_id, user_id, title, file_path, file.filename, file_ext[1:])
        )
        await db.commit()

        # Get created document with timestamp
        async with db.execute(
            "SELECT id, title, original_filename, file_type, created_at FROM documents WHERE id = ?",
            (doc_id,)
        ) as cursor:
            doc = await cursor.fetchone()

    # Process in background
    background_tasks.add_task(
        process_document_background,
        doc_id,
        user_id,
        markdown_content,
        title
    )

    return dict(doc)


async def process_document_background(doc_id: str, user_id: int, markdown: str, title: str):
    """Process document in background: chunk, embed, extract entities."""
    import time
    progress = get_progress_emitter()

    # Record start time for duration calculation
    start_time = time.time()

    try:
        logger.info("Starting background processing for doc %s", doc_id)
        await progress.emit_and_save(doc_id, user_id, "started", "Starting document processing", {"title": title})

        # Create document node in Neo4j
        neo4j = await get_neo4j_client()
        await neo4j.create_document_node(doc_id, user_id, title)
        logger.info("Created document node in Neo4j for %s", doc_id)
        await progress.emit_and_save(doc_id, user_id, "document_created", "Document created", {"stage": "document_created"})

        # Chunk the document
        await progress.emit_and_save(doc_id, user_id, "chunking", "Chunking document...", {"stage": "chunking", "current": 0, "total": 1})
        chunks = chunk_markdown(markdown, doc_id, user_id)
        logger.info("Created %d chunks for doc %s", len(chunks), doc_id)
        await progress.emit_and_save(doc_id, user_id, "chunking", f"Created {len(chunks)} chunks", {"stage": "chunking", "current": 1, "total": 1, "percent": 100})

        if not chunks:
            logger.warning("No chunks created for doc %s", doc_id)
            await progress.emit_and_save(doc_id, user_id, "error", "No content could be extracted from the document", {"stage": "error"})
            return

        # Get embedding service
        embedding_service = await get_embedding_service()

        # Prepare for batch processing
        chunk_contents = [c.content for c in chunks]
        chunk_ids = [c.chunk_id for c in chunks]

        # Generate embeddings with progress. embed_batch may raise
        # EmbeddingServiceError after all retries are exhausted — we surface that
        # to the SSE channel so the UI can show a real error rather than a stuck
        # progress bar (or worse, silently-stored zero vectors).
        await progress.emit_and_save(doc_id, user_id, "embedding", "Creating embeddings...", {"stage": "embedding", "current": 0, "total": len(chunks)})
        try:
            embeddings = await embedding_service.embed_batch(chunk_contents)
        except EmbeddingServiceError as embed_err:
            logger.error("Embedding failed for doc %s: %s", doc_id, embed_err, exc_info=True)
            await progress.emit_and_save(
                doc_id, user_id, "error",
                f"Embedding failed: {embed_err}",
                {
                    "stage": "embedding",
                    "error": str(embed_err),
                    "error_stage": "embedding",
                    "retryable": True,
                    "percent": 0,
                },
            )
            return
        await progress.emit_and_save(doc_id, user_id, "embedding", f"Created {len(embeddings)} embeddings", {"stage": "embedding", "current": len(chunks), "total": len(chunks), "percent": 100})

        # Prepare metadata - ChromaDB stores all values as strings
        metadatas = []
        for chunk in chunks:
            metadatas.append({
                "user_id": str(user_id),
                "document_id": doc_id,
                "hierarchy_level": str(chunk.hierarchy.level),
                "hierarchy_path": ", ".join(chunk.hierarchy.path),
                "prev_chunk_id": chunk.position.prev_chunk_id or "",
                "next_chunk_id": chunk.position.next_chunk_id or ""
            })

        # Store in ChromaDB
        chroma = get_chroma_client()
        chroma.add_chunks(chunk_ids, chunk_contents, embeddings, metadatas)
        await progress.emit_and_save(doc_id, user_id, "stored", "Stored in vector database", {"stage": "stored", "percent": 50})

        # Build BM25 index for user (hybrid search)
        bm25 = get_bm25_service()
        bm25.add_to_index(user_id, chunk_contents, chunk_ids)

        # Store chunks in SQLite
        async with get_db() as db:
            for chunk in chunks:
                hierarchy_path_str = ",".join(chunk.hierarchy.path) if chunk.hierarchy.path else ""
                await db.execute(
                    """INSERT INTO chunks
                       (chunk_id, document_id, user_id, content, hierarchy_path, level,
                        prev_chunk_id, next_chunk_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (chunk.chunk_id, doc_id, user_id, chunk.content,
                     hierarchy_path_str, chunk.hierarchy.level,
                     chunk.position.prev_chunk_id, chunk.position.next_chunk_id)
                )
            await db.commit()
        logger.info("Stored %d chunks in SQLite for doc %s", len(chunks), doc_id)

        # Create chunk nodes in Neo4j and link them
        await progress.emit_and_save(doc_id, user_id, "graph", "Building knowledge graph...", {"stage": "graph", "current": 0, "total": len(chunks)})
        for i, chunk in enumerate(chunks):
            await neo4j.create_chunk_node(
                chunk.chunk_id, doc_id, user_id, chunk.content,
                chunk.hierarchy.path, chunk.position.start_line
            )
            await neo4j.create_chunk_links(
                chunk.chunk_id,
                chunk.position.prev_chunk_id,
                chunk.position.next_chunk_id
            )
            if i % 5 == 0:  # Emit progress every 5 chunks
                await progress.emit_and_save(doc_id, user_id, "graph", f"Processing chunk {i+1}/{len(chunks)}", {"stage": "graph", "current": i+1, "total": len(chunks), "percent": int((i+1)/len(chunks)*30) + 50})
        await progress.emit_and_save(doc_id, user_id, "graph", "Knowledge graph building complete", {"stage": "graph", "current": len(chunks), "total": len(chunks), "percent": 80})

        # Extract entities and relations
        logger.info("Starting entity extraction with LLM for doc %s", doc_id)
        total_chunks = len(chunks)
        await progress.emit_and_save(doc_id, user_id, "entity_extraction", f"Extracting entities from {total_chunks} chunks with LLM...", {
            "stage": "entity_extraction",
            "current": 0,
            "total": total_chunks,
            "percent": 0
        })

        extractor = await get_entity_extractor()
        settings = get_settings()

        # Use rule extraction only if explicitly enabled (faster LLM-only mode by default)
        use_rule_extraction = getattr(settings, 'USE_RULE_EXTRACTION', False)

        # Process chunks in batches - use configured batch size
        batch_size = settings.ENTITY_BATCH_SIZE
        all_entities = []
        all_relations = []
        all_chunk_entities = []

        for batch_start in range(0, total_chunks, batch_size):
            batch_end = min(batch_start + batch_size, total_chunks)
            batch_chunks = chunks[batch_start:batch_end]

            # Show which chunks are being processed
            chunk_nums = [f"{i+1}" for i in range(batch_start, batch_end)]
            await progress.emit_and_save(doc_id, user_id, "entity_extraction",
                f"Processing chunks {', '.join(chunk_nums)} ({batch_end}/{total_chunks})", {
                "stage": "entity_extraction",
                "current": batch_end,
                "total": total_chunks,
                "percent": int(batch_end / total_chunks * 60)
            })

            # Process this batch
            batch_result = await extractor.process_chunks(batch_chunks, use_rule_extraction)
            all_entities.extend(batch_result["entities"])
            all_relations.extend(batch_result["relations"])
            all_chunk_entities.extend(batch_result["chunk_entities"])

            # Show some extracted entities
            recent_entities = batch_result["entities"][-5:] if batch_result["entities"] else []
            entity_names = [e.name for e in recent_entities]
            if entity_names:
                logger.info("Batch %d: Found entities: %s", batch_start // batch_size + 1, entity_names[:3])

        # Deduplicate entities
        entity_dict = {}
        for entity in all_entities:
            key = (entity.name.lower(), entity.type)
            if key not in entity_dict:
                entity_dict[key] = entity
        unique_entities = list(entity_dict.values())

        extraction_result = {
            "entities": unique_entities,
            "relations": all_relations,
            "chunk_entities": all_chunk_entities
        }

        logger.info("Extracted %d entities and %d relations for doc %s",
                    len(extraction_result["entities"]),
                    len(extraction_result["relations"]),
                    doc_id)
        await progress.emit_and_save(doc_id, user_id, "entity_extraction",
            f"LLM extracted {len(extraction_result['entities'])} entities", {
            "stage": "entity_extraction",
            "current": total_chunks,
            "total": total_chunks,
            "percent": 70,
            "entities_sample": [e.name for e in extraction_result["entities"][:10]]
        })

        # Show extracted entities in progress
        sample_entities = [e.name for e in extraction_result["entities"][:15]]
        await progress.emit_and_save(doc_id, user_id, "entities", f"Found {len(extraction_result['entities'])} entities: {', '.join(sample_entities[:5])}...", {
            "stage": "entities",
            "current": 0,
            "total": len(extraction_result['entities']),
            "percent": 75,
            "entities": sample_entities
        })

        # Create entity nodes in a single UNWIND batch (one round-trip, not N).
        logger.info("Creating %d entity nodes (batch)", len(extraction_result["entities"]))
        entity_payloads = [
            {"name": e.name, "type": e.type, "description": e.description}
            for e in extraction_result["entities"]
        ]
        upserted = await neo4j.create_entities_batch(entity_payloads, user_id)
        logger.info("Upserted %d entities", upserted)
        await progress.emit_and_save(doc_id, user_id, "entities",
            f"Saved {len(extraction_result['entities'])} entities (batch)", {
            "stage": "entities",
            "current": len(extraction_result["entities"]),
            "total": len(extraction_result['entities']),
            "percent": 80
        })

        # Create MENTIONS relations in a single UNWIND batch.
        chunk_entities = extraction_result.get("chunk_entities", [])
        links = [
            {"chunk_id": cd.get("chunk_id"), "entity_name": e.name}
            for cd in chunk_entities
            for e in cd.get("entities", [])
            if cd.get("chunk_id") and e.name
        ]
        logger.info("Creating %d MENTIONS links (batch)", len(links))
        mentions_count = await neo4j.link_chunks_to_entities_batch(links, user_id)
        logger.info("Created %d MENTIONS relations", mentions_count)

        # Show extracted relations
        sample_relations = [(r.source, r.target, r.relation_type) for r in extraction_result["relations"][:5]]
        await progress.emit_and_save(doc_id, user_id, "relations",
            f"Creating {len(extraction_result['relations'])} relations", {
            "stage": "relations",
            "current": 0,
            "total": len(extraction_result['relations']),
            "percent": 90,
            "relations_sample": sample_relations
        })

        # Create relations between entities in a single UNWIND batch.
        relation_payloads = [
            {
                "source": r.source,
                "target": r.target,
                "relation_type": r.relation_type,
            }
            for r in extraction_result["relations"]
        ]
        logger.info("Creating %d RELATES_TO edges (batch)", len(relation_payloads))
        rels_count = await neo4j.create_relations_batch(relation_payloads, user_id)
        logger.info("Created %d RELATES_TO relations", rels_count)
        await progress.emit_and_save(doc_id, user_id, "relations",
            f"Saved {len(extraction_result['relations'])} relations (batch)", {
            "stage": "relations",
            "current": len(extraction_result["relations"]),
            "total": len(extraction_result['relations']),
            "percent": 95
        })

        logger.info("Document %s background processing completed", doc_id)

        # Calculate duration
        duration = time.time() - start_time
        duration_str = format_duration(duration)

        # Emit completion
        await progress.emit_and_save(
            doc_id, user_id, "complete", f"Document processing complete ({duration_str})",
            {"stage": "complete", "percent": 100, "duration": duration_str},
            entity_count=len(extraction_result['entities']),
            relation_count=len(extraction_result['relations'])
        )

    except Exception as e:
        # Log error but don't fail the upload
        import traceback
        logger.error("Background processing error for doc %s: %s", doc_id, e, exc_info=True)
        # Emit error event
        try:
            progress = get_progress_emitter()
            await progress.emit_and_save(doc_id, user_id, "error", f"Error: {str(e)}", {"stage": "error", "error": str(e)})
        except Exception as emitter_error:
            logger.warning("Failed to emit error progress for doc %s: %s", doc_id, emitter_error)


@router.get("", response_model=List[DocumentResponse])
async def list_documents(
    current_user: dict = Depends(get_current_user),
    skip: int = 0,
    limit: int = 100,
    tag: Optional[str] = None
):
    """List user's documents. If `tag` is supplied, only documents with
    that (normalised) tag are returned. Tag matches against the stored
    normalised form, so callers can pass any case or include/exclude a
    leading '#' — we normalise here too."""
    user_id = current_user["id"]

    if tag is not None:
        normalised = normalize_tag(tag)
        if not normalised:
            # Don't surprise the user with a 400 — an empty filter is
            # treated as "no filter", matching the principle that the
            # chip-clear button should always work.
            tag = None
        else:
            tag = normalised

    if tag:
        # Filter at the SQL layer so we don't pull tags for docs we'd
        # discard. Inner join keeps only docs that actually have the tag.
        sql = """SELECT d.id, d.title, d.original_filename, d.file_type, d.created_at
                 FROM documents d
                 INNER JOIN document_tags t
                   ON t.document_id = d.id AND t.user_id = d.user_id
                 WHERE d.user_id = ? AND t.tag = ?
                 ORDER BY d.created_at DESC LIMIT ? OFFSET ?"""
        params = (user_id, tag, limit, skip)
    else:
        sql = """SELECT id, title, original_filename, file_type, created_at
                 FROM documents WHERE user_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?"""
        params = (user_id, limit, skip)

    async with get_db() as db:
        async with db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()

    documents = [dict(row) for row in rows]
    if not documents:
        return documents

    # Two-query approach: fetch tags for the page in one shot and stitch
    # them on. Cheaper than N+1 and simpler than GROUP_CONCAT (which
    # would force us to learn aiosqlite's GROUP_CONCAT separator).
    doc_ids = [d["id"] for d in documents]
    placeholders = ",".join("?" * len(doc_ids))
    tags_sql = f"""SELECT document_id, tag FROM document_tags
                   WHERE user_id = ? AND document_id IN ({placeholders})"""
    tags_by_doc: dict = {d_id: [] for d_id in doc_ids}
    async with get_db() as db:
        async with db.execute(tags_sql, (user_id, *doc_ids)) as cursor:
            tag_rows = await cursor.fetchall()
    for tr in tag_rows:
        tags_by_doc[tr["document_id"]].append(tr["tag"])

    for d in documents:
        d["tags"] = tags_by_doc.get(d["id"], [])

    return documents


@router.get("/{doc_id}/detail")
async def get_document_detail(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Return everything a "knowledge unit" page needs for one document.

    Aggregates SQLite data (metadata, tags, chunk count, sample chunks)
    with Neo4j data (key entities, related documents) into a single
    payload so the front-end renders in one round-trip.

    Multi-tenant: 404 on missing OR not-owned doc (never 401/403, to
    avoid leaking which doc_ids exist).
    """
    user_id = current_user["id"]

    async with get_db() as db:
        async with db.execute(
            "SELECT id, title, original_filename, file_type, created_at "
            "FROM documents WHERE id = ? AND user_id = ?",
            (doc_id, user_id),
        ) as cursor:
            doc_row = await cursor.fetchone()
        if doc_row is None:
            raise HTTPException(status_code=404, detail="Document not found")

        async with db.execute(
            "SELECT tag FROM document_tags "
            "WHERE document_id = ? AND user_id = ? ORDER BY tag ASC",
            (doc_id, user_id),
        ) as cursor:
            tag_rows = await cursor.fetchall()
        tags = [r["tag"] for r in tag_rows]

        async with db.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE document_id = ? AND user_id = ?",
            (doc_id, user_id),
        ) as cursor:
            count_row = await cursor.fetchone()
        chunk_count = int(count_row["n"]) if count_row else 0

        async with db.execute(
            "SELECT chunk_id, content, hierarchy_path "
            "FROM chunks WHERE document_id = ? AND user_id = ? "
            "ORDER BY created_at ASC LIMIT 3",
            (doc_id, user_id),
        ) as cursor:
            chunk_rows = await cursor.fetchall()
        sample_chunks = [
            {
                "chunk_id": r["chunk_id"],
                "content": (r["content"] or "")[:400],
                "hierarchy_path": r["hierarchy_path"] or "",
            }
            for r in chunk_rows
        ]

    neo4j = await get_neo4j_client()
    key_entities = await neo4j.get_doc_entities(doc_id, user_id, limit=10)
    related_documents = await neo4j.get_related_documents(doc_id, user_id, limit=5)

    return {
        "document": {
            "id": doc_row["id"],
            "title": doc_row["title"] or doc_row["original_filename"],
            "original_filename": doc_row["original_filename"],
            "file_type": doc_row["file_type"],
            "created_at": doc_row["created_at"],
            "tags": tags,
        },
        "stats": {
            "chunk_count": chunk_count,
            "key_entity_count": len(key_entities),
            "related_document_count": len(related_documents),
        },
        "key_entities": key_entities,
        "related_documents": related_documents,
        "sample_chunks": sample_chunks,
    }


# -------------------------------------------------------------------------
# Cluster map (PCA-projected 2D embedding view of all the user's docs)
# -------------------------------------------------------------------------
#
# Computes one centroid embedding per document (average of up to N
# chunks' embeddings), then projects to 2D with a hand-rolled PCA so the
# front-end can render a scatter plot of "what topics do my docs cover,
# and which ones are similar". The visual is a 2D landscape where
# related docs cluster together and unrelated ones drift apart.
#
# Why PCA and not UMAP/t-SNE?
#   * PCA is deterministic and explainable (axis-aligned variance).
#   * No extra dependency — numpy.linalg.svd is already in the env.
#   * For a personal knowledge base (~10-200 docs) PCA is plenty.
#   * UMAP/t-SNE would give prettier clusters but at the cost of
#     non-determinism (every load shuffles the layout) and 50+ MB of
#     extra deps.
_MAX_CHUNKS_PER_DOC_FOR_CLUSTER = 5


def _pca_2d(X: np.ndarray) -> np.ndarray:
    """Project an (N, D) matrix to (N, 2) via 2-component PCA.

    Steps:
      1. Centre the data (subtract the per-feature mean).
      2. SVD: X_centered = U · S · Vt; the top-2 principal axes are
         the first 2 rows of Vt.
      3. Project by multiplying X_centered by Vt[:2].T.

    Raises ValueError on malformed input (1-D, N < 2, etc.) so the
    endpoint can return 400 rather than 500.
    """
    if X.ndim != 2:
        raise ValueError(f"_pca_2d expects 2-D matrix, got ndim={X.ndim}")
    n, d = X.shape
    if n < 2:
        raise ValueError(f"_pca_2d needs >= 2 rows, got {n}")
    if d < 2:
        raise ValueError(f"_pca_2d needs >= 2 features, got {d}")

    X_centered = X - X.mean(axis=0, keepdims=True)
    # full_matrices=False keeps the small-matrix fast path; we only
    # need the first 2 right singular vectors.
    _, _, Vt = np.linalg.svd(X_centered, full_matrices=False)
    components = Vt[:2]  # shape (2, d)
    return X_centered @ components.T  # shape (n, 2)


def _doc_centroid_embedding(vectors: List[List[float]]) -> Optional[List[float]]:
    """Element-wise mean of a doc's per-chunk embeddings.

    Returns None for an empty input so the caller can skip the doc
    rather than averaging nothing into a zero vector (which would
    project to the origin and lie about the doc's content).
    """
    if not vectors:
        return None
    arr = np.asarray(vectors, dtype=np.float64)
    return arr.mean(axis=0).tolist()


async def _embed_chunks_for_centroid(contents: List[str]) -> List[List[float]]:
    """Thin wrapper around the embedding service used by the cluster
    endpoint. Kept as a module-level function so tests can patch it
    without monkey-patching the embedding service globally.

    Empty / blank chunks are filtered out before hitting the embedding
    API — they would just waste tokens and return zero-vectors.
    """
    cleaned = [c for c in contents if c and c.strip()]
    if not cleaned:
        return []
    svc = await get_embedding_service()
    return await svc.embed_batch(cleaned)


@router.get("/cluster-map")
async def get_cluster_map(
    current_user: dict = Depends(get_current_user),
):
    """Return each of the user's documents as a (doc_id, title, x, y)
    point in 2D semantic space.

    Pipeline:
      1. Load all docs (id, title, file_type) for the user.
      2. Load all chunks (document_id, content) for the user.
      3. For each doc, take the first N chunks' content, embed them,
         average → centroid.
      4. Stack centroids into a (n_docs, embed_dim) matrix.
      5. PCA → (n_docs, 2).
      6. Return one point per doc.

    Edge cases:
      * 0 docs → `{points: []}`
      * 1 doc  → `{points: []}` (a one-point map is useless)
      * any doc with no chunks → skipped (not projected as a zero-vector)
      * embedding service failure → 502 (the rest of the API would
        also be broken in that case)
    """
    user_id = current_user["id"]

    async with get_db() as db:
        async with db.execute(
            "SELECT id, title, file_type, original_filename "
            "FROM documents WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ) as cursor:
            doc_rows = await cursor.fetchall()
        async with db.execute(
            "SELECT document_id, content FROM chunks "
            "WHERE user_id = ? ORDER BY created_at ASC",
            (user_id,),
        ) as cursor:
            chunk_rows = await cursor.fetchall()

    if not doc_rows:
        return {"points": []}

    # Group chunks by document, preserving insertion order (oldest first
    # so the "first 5" is the intro of each doc, not the last paragraph).
    chunks_by_doc: dict = {}
    for r in chunk_rows:
        chunks_by_doc.setdefault(r["document_id"], []).append(r["content"])

    points: List[dict] = []
    centroids: List[List[float]] = []
    centroid_meta: List[dict] = []  # aligned with `centroids`

    for d in doc_rows:
        contents = chunks_by_doc.get(d["id"], [])[:_MAX_CHUNKS_PER_DOC_FOR_CLUSTER]
        if not contents:
            continue
        vecs = await _embed_chunks_for_centroid(contents)
        centroid = _doc_centroid_embedding(vecs)
        if centroid is None:
            continue
        centroids.append(centroid)
        centroid_meta.append({
            "doc_id": d["id"],
            "title": d["title"] or d["original_filename"],
            "file_type": d["file_type"] or "",
        })

    if len(centroids) < 2:
        # Can't meaningfully project fewer than 2 points.
        return {"points": []}

    try:
        projected = _pca_2d(np.asarray(centroids, dtype=np.float64))
    except ValueError as e:
        # Malformed input (all chunks blank → zero-vec stack) — return
        # empty rather than 500.
        logger.warning("cluster-map: PCA failed (%s); returning empty", e)
        return {"points": []}

    for meta, (x, y) in zip(centroid_meta, projected):
        points.append({
            **meta,
            "x": float(x),
            "y": float(y),
        })

    return {"points": points}


@router.delete("/{doc_id}")
async def delete_document(
    doc_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Delete a document and all associated data."""
    user_id = current_user["id"]

    # Get document info
    async with get_db() as db:
        async with db.execute(
            "SELECT file_path FROM documents WHERE id = ? AND user_id = ?",
            (doc_id, user_id)
        ) as cursor:
            doc = await cursor.fetchone()

    if not doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )

    # Delete file
    try:
        if os.path.exists(doc["file_path"]):
            os.remove(doc["file_path"])
    except Exception:
        pass

    # Delete from ChromaDB
    chroma = get_chroma_client()
    chroma.delete_document_chunks(doc_id, user_id)

    # Delete from Neo4j
    neo4j = await get_neo4j_client()
    await neo4j.delete_document(doc_id, user_id)

    # Delete from SQLite
    async with get_db() as db:
        # First delete chunks for this document
        await db.execute(
            "DELETE FROM chunks WHERE document_id = ? AND user_id = ?",
            (doc_id, user_id)
        )
        # Then delete the document
        await db.execute(
            "DELETE FROM documents WHERE id = ? AND user_id = ?",
            (doc_id, user_id)
        )
        await db.commit()

    return {"message": "Document deleted successfully"}


@router.get("/{doc_id}/chunks", response_model=List[ChunkResponse])
async def get_document_chunks(
    doc_id: str,
    current_user: dict = Depends(get_current_user)
):
    """Get chunks for a document."""
    user_id = current_user["id"]
    logger.info("Getting chunks for doc_id=%s, user_id=%d", doc_id, user_id)

    try:
        async with get_db() as db:
            async with db.execute(
                """SELECT chunk_id, content, hierarchy_path, level,
                          prev_chunk_id, next_chunk_id, created_at
                   FROM chunks WHERE document_id = ? AND user_id = ? ORDER BY created_at""",
                (doc_id, user_id)
            ) as cursor:
                rows = await cursor.fetchall()

        logger.info("Found %d chunks in database for doc %s", len(rows), doc_id)

        if not rows:
            # Check if document exists
            async with get_db() as db:
                async with db.execute(
                    "SELECT id FROM documents WHERE id = ? AND user_id = ?",
                    (doc_id, user_id)
                ) as cursor:
                    doc = await cursor.fetchone()

            if doc:
                logger.info("Document %s exists but no chunks yet - background processing may still be running", doc_id)
                return []  # Return empty list instead of 404
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Document not found"
                )

        chunks = []
        for row in rows:
            row_dict = dict(row)
            row_dict["hierarchy"] = {
                "path": row_dict["hierarchy_path"].split(", ") if row_dict["hierarchy_path"] else [],
                "level": row_dict["level"]
            }
            chunks.append(row_dict)

        return chunks
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error getting chunks for doc %s: %s", doc_id, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get chunks: {str(e)}"
        )


# =========================================================================
# Document tags
# =========================================================================
#
# A "tag" is a short user-defined label attached to one document. Storage
# is normalised (lowercase, no leading '#') so that visually identical
# inputs collide into the same row — both for a clean filter UI and to
# make the UNIQUE(document_id, user_id, tag) constraint meaningful.
#
# All endpoints require the document to belong to the calling user; we
# enforce ownership with a WHERE user_id = ? on every read/write, so a
# user can never read or mutate another user's tags by guessing ids.

async def _verify_doc_ownership(db, doc_id: str, user_id: int) -> None:
    """Raise 404 if the document is missing or owned by another user.

    Centralised so every tag endpoint can call it before doing any work —
    the alternative (silently no-op on missing docs) leaks ownership
    information and makes UI state confusing.
    """
    async with db.execute(
        "SELECT 1 FROM documents WHERE id = ? AND user_id = ?",
        (doc_id, user_id)
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found",
        )


@router.get("/{doc_id}/tags", response_model=List[str])
async def list_document_tags(
    doc_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List the tags attached to a single document (alphabetical)."""
    user_id = current_user["id"]
    async with get_db() as db:
        await _verify_doc_ownership(db, doc_id, user_id)
        async with db.execute(
            """SELECT tag FROM document_tags
               WHERE document_id = ? AND user_id = ?
               ORDER BY tag""",
            (doc_id, user_id)
        ) as cursor:
            rows = await cursor.fetchall()
    return [r["tag"] for r in rows]


@router.post("/{doc_id}/tags", response_model=List[str])
async def add_document_tag(
    doc_id: str,
    body: TagCreate,
    current_user: dict = Depends(get_current_user),
):
    """Attach a tag to a document. Idempotent — re-adding the same tag is
    a no-op (the UNIQUE constraint catches it). Returns the full sorted
    tag list for the document so the client can replace its local state
    in one round-trip without a follow-up GET."""
    user_id = current_user["id"]
    tag = normalize_tag(body.tag)
    if not tag:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tag must contain at least one non-whitespace, non-'#' character",
        )

    async with get_db() as db:
        await _verify_doc_ownership(db, doc_id, user_id)
        # INSERT OR IGNORE so the same tag can be re-submitted without 500s.
        await db.execute(
            """INSERT OR IGNORE INTO document_tags (document_id, user_id, tag)
               VALUES (?, ?, ?)""",
            (doc_id, user_id, tag),
        )
        await db.commit()
        async with db.execute(
            """SELECT tag FROM document_tags
               WHERE document_id = ? AND user_id = ?
               ORDER BY tag""",
            (doc_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()

    return [r["tag"] for r in rows]


@router.delete("/{doc_id}/tags/{tag:path}", response_model=List[str])
async def remove_document_tag(
    doc_id: str,
    tag: str,
    current_user: dict = Depends(get_current_user),
):
    """Detach a tag from a document. The tag is normalised server-side so
    callers can pass whatever shape they have on hand. The response is
    the updated tag list (may be empty). Returns 404 only if the document
    itself is missing — removing a non-existent tag is a no-op (200)."""
    user_id = current_user["id"]
    normalised = normalize_tag(tag)
    if not normalised:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tag must contain at least one non-whitespace, non-'#' character",
        )

    async with get_db() as db:
        await _verify_doc_ownership(db, doc_id, user_id)
        await db.execute(
            """DELETE FROM document_tags
               WHERE document_id = ? AND user_id = ? AND tag = ?""",
            (doc_id, user_id, normalised),
        )
        await db.commit()
        async with db.execute(
            """SELECT tag FROM document_tags
               WHERE document_id = ? AND user_id = ?
               ORDER BY tag""",
            (doc_id, user_id),
        ) as cursor:
            rows = await cursor.fetchall()

    return [r["tag"] for r in rows]
