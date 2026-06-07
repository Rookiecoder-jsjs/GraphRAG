"""Neo4j client for knowledge graph operations."""
import logging
from typing import List, Dict, Any, Optional
from neo4j import AsyncGraphDatabase, AsyncSession
from contextlib import asynccontextmanager

from app.config import get_settings

logger = logging.getLogger(__name__)


class Neo4jClient:
    """Client for Neo4j graph database operations."""

    def __init__(self):
        self.settings = get_settings()
        self._driver = None

    async def connect(self):
        """Initialize Neo4j connection."""
        if self._driver is None:
            self._driver = AsyncGraphDatabase.driver(
                self.settings.NEO4J_URI,
                auth=(self.settings.NEO4J_USER, self.settings.NEO4J_PASSWORD)
            )

    async def close(self):
        """Close Neo4j connection."""
        if self._driver:
            await self._driver.close()
            self._driver = None

    @asynccontextmanager
    async def session(self):
        """Get a database session."""
        if self._driver is None:
            await self.connect()
        async with self._driver.session() as session:
            yield session

    async def create_user_node(self, user_id: int, username: str):
        """Create a user node."""
        async with self.session() as session:
            await session.run("""
                MERGE (u:User {user_id: $user_id})
                SET u.username = $username, u.created_at = datetime()
            """, user_id=user_id, username=username)

    async def create_document_node(self, doc_id: str, user_id: int, title: str):
        """Create a document node and link to user."""
        async with self.session() as session:
            # First ensure User node exists
            await session.run("""
                MERGE (u:User {user_id: $user_id})
            """, user_id=user_id)
            # Then create Document and link to User
            await session.run("""
                MATCH (u:User {user_id: $user_id})
                MERGE (d:Document {doc_id: $doc_id})
                SET d.title = $title, d.created_at = datetime(), d.user_id = $user_id
                MERGE (u)-[:OWNS]->(d)
            """, doc_id=doc_id, user_id=user_id, title=title)

    async def create_chunk_node(self, chunk_id: str, document_id: str, user_id: int,
                                content: str, hierarchy_path: List[str], position: int):
        """Create a chunk node and link to document.

        Stores the full chunk text on the Chunk node so that graph-only queries
        (e.g. browsing the visualization) can show the text without an extra
        round-trip to ChromaDB. The previous [:1000] truncation silently dropped
        data on long chunks.
        """
        async with self.session() as session:
            # Ensure User exists for this user_id (idempotent).
            await session.run("""
                MERGE (u:User {user_id: $user_id})
            """, user_id=user_id)

            # Ensure Document exists, scoped to this user.
            await session.run("""
                MERGE (d:Document {doc_id: $document_id})
                SET d.user_id = $user_id
            """, document_id=document_id, user_id=user_id)

            # Create Chunk and CONTAINS link in a single query so the
            # relationship can never be created without both endpoints.
            await session.run("""
                MERGE (d:Document {doc_id: $document_id})
                MERGE (c:Chunk {chunk_id: $chunk_id})
                SET c.content = $content,
                    c.hierarchy_path = $hierarchy_path,
                    c.position = $position,
                    c.user_id = $user_id,
                    c.created_at = datetime()
                MERGE (d)-[:CONTAINS]->(c)
            """, chunk_id=chunk_id, document_id=document_id, user_id=user_id,
                content=content, hierarchy_path=hierarchy_path, position=position)

    async def create_chunk_links(self, chunk_id: str, prev_chunk_id: Optional[str],
                                 next_chunk_id: Optional[str]):
        """Create NEXT links between chunks."""
        async with self.session() as session:
            if prev_chunk_id:
                await session.run("""
                    MATCH (prev:Chunk {chunk_id: $prev_id})
                    MATCH (curr:Chunk {chunk_id: $chunk_id})
                    MERGE (prev)-[:NEXT]->(curr)
                """, prev_id=prev_chunk_id, chunk_id=chunk_id)
            if next_chunk_id:
                await session.run("""
                    MATCH (curr:Chunk {chunk_id: $chunk_id})
                    MATCH (next:Chunk {chunk_id: $next_id})
                    MERGE (curr)-[:NEXT]->(next)
                """, chunk_id=chunk_id, next_id=next_chunk_id)

    async def create_entity(self, name: str, entity_type: str, description: Optional[str],
                           user_id: int) -> str:
        """Create or update an entity node."""
        async with self.session() as session:
            result = await session.run("""
                MERGE (e:Entity {name: $name, user_id: $user_id})
                SET e.type = $entity_type,
                    e.description = COALESCE($description, e.description),
                    e.updated_at = datetime()
                RETURN elementId(e) as entity_id
            """, name=name, entity_type=entity_type, description=description, user_id=user_id)
            record = await result.single()
            return record["entity_id"] if record else None

    # ---------- Entity curation (PATCH / DELETE / merge) ---------------
    #
    # These power the human-in-the-loop UI for cleaning up LLM-extracted
    # entities. They are the only operations that allow destructive
    # changes, so they all enforce the (name, user_id) ownership tuple
    # strictly — there is no way to mutate another user's data.

    async def update_entity(
        self,
        name: str,
        user_id: int,
        entity_type: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """PATCH an existing entity. Returns the updated row, or None if not found.

        Only the fields explicitly provided are touched; `None` means "leave alone".
        An empty-string description is treated as "clear the description"
        because the Pydantic model allows it as a meaningful value.
        """
        sets = ["e.updated_at = datetime()"]
        params: Dict[str, Any] = {"name": name, "user_id": user_id}
        if entity_type is not None:
            sets.append("e.type = $entity_type")
            params["entity_type"] = entity_type
        if description is not None:
            # Empty string → clear it; any other string → set it.
            sets.append("e.description = $description")
            params["description"] = description if description != "" else None

        query = f"""
            MATCH (e:Entity {{name: $name, user_id: $user_id}})
            SET {', '.join(sets)}
            RETURN e.name AS name, e.type AS type, coalesce(e.description, '') AS description
        """
        async with self.session() as session:
            result = await session.run(query, **params)
            record = await result.single()
        if not record:
            return None
        return {
            "name": record["name"],
            "type": record["type"],
            "description": record["description"],
        }

    async def delete_entity(self, name: str, user_id: int) -> int:
        """Delete one entity and clean up all references to it.

        Order matters: MENTIONS edges first, then RELATES_TO edges, then
        the entity itself. Returns the number of entity nodes deleted
        (0 if the entity didn't exist; 1 on success).
        """
        async with self.session() as session:
            # 1. MENTIONS: chunk -> entity. The chunk still exists; we just
            #    stop the chunk "mentioning" this entity.
            await session.run(
                """
                MATCH (:Chunk)-[r:MENTIONS]->(e:Entity {name: $name, user_id: $user_id})
                DELETE r
                """,
                name=name, user_id=user_id,
            )
            # 2. RELATES_TO: any edge touching this entity, both directions.
            #    We detach by deleting the edges, not the other endpoint.
            await session.run(
                """
                MATCH (e:Entity {name: $name, user_id: $user_id})-[r:RELATES_TO]-(:Entity)
                DELETE r
                """,
                name=name, user_id=user_id,
            )
            # 3. Finally the entity itself.
            result = await session.run(
                """
                MATCH (e:Entity {name: $name, user_id: $user_id})
                WITH e, count(e) AS c
                DELETE e
                RETURN c AS deleted
                """,
                name=name, user_id=user_id,
            )
            record = await result.single()
        return int(record["deleted"]) if record else 0

    async def merge_entities(
        self,
        source_name: str,
        target_name: str,
        user_id: int,
    ) -> Dict[str, Any]:
        """Merge `source_name` into `target_name`.

        What happens:
          - All MENTIONS edges that pointed to source are re-pointed to
            target. If a chunk already had a MENTIONS edge to target,
            the source edge is dropped (dedup).
          - All RELATES_TO edges incident to source are re-pointed to
            target with the same relation_type and properties. If target
            already has an edge of the same relation_type to the same
            other entity, the source edge is dropped (dedup).
          - Source entity is deleted.

        Returns a count summary. Raises LookupError if either name doesn't
        exist; raises ValueError if source == target.
        """
        if source_name == target_name:
            raise ValueError("source and target must be different entities")

        async with self.session() as session:
            # Verify both exist (and belong to this user) before doing
            # anything destructive.
            result = await session.run(
                """
                MATCH (e:Entity {user_id: $user_id})
                WHERE e.name IN [$source, $target]
                RETURN e.name AS name
                """,
                source=source_name, target=target_name, user_id=user_id,
            )
            found = {record["name"] async for record in result}
            if source_name not in found:
                raise LookupError(f"source entity not found: {source_name!r}")
            if target_name not in found:
                raise LookupError(f"target entity not found: {target_name!r}")

            # 1. MENTIONS: re-point from source → target, deduping.
            result = await session.run(
                """
                MATCH (c:Chunk)-[r:MENTIONS]->(src:Entity {name: $source, user_id: $user_id})
                OPTIONAL MATCH (c)-[existing:MENTIONS]->(tgt:Entity {name: $target, user_id: $user_id})
                WITH c, r, existing, tgt
                DELETE r
                FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
                    MERGE (c)-[:MENTIONS]->(tgt)
                )
                RETURN count(r) AS removed
                """,
                source=source_name, target=target_name, user_id=user_id,
            )
            record = await result.single()
            mentions_rewritten = int(record["removed"]) if record else 0

            # 2. Outgoing RELATES_TO: (source)-[r]->(other) becomes
            #    (target)-[new]->(other) with same relation_type + props.
            result = await session.run(
                """
                MATCH (src:Entity {name: $source, user_id: $user_id})-[r:RELATES_TO]->(other:Entity {user_id: $user_id})
                WHERE other.name <> $target
                WITH src, r, other
                OPTIONAL MATCH (tgt:Entity {name: $target, user_id: $user_id})-[existing:RELATES_TO {relation_type: r.relation_type}]->(other)
                WITH r, existing, tgt, other
                DELETE r
                FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
                    CREATE (tgt)-[new:RELATES_TO {relation_type: r.relation_type}]->(other)
                    SET new = properties(r)
                )
                RETURN count(r) AS removed
                """,
                source=source_name, target=target_name, user_id=user_id,
            )
            record = await result.single()
            outgoing_rewritten = int(record["removed"]) if record else 0

            # 3. Incoming RELATES_TO: (other)-[r]->(source) becomes
            #    (other)-[new]->(target) with same relation_type + props.
            result = await session.run(
                """
                MATCH (other:Entity {user_id: $user_id})-[r:RELATES_TO]->(src:Entity {name: $source, user_id: $user_id})
                WHERE other.name <> $target
                WITH src, r, other
                OPTIONAL MATCH (other)-[existing:RELATES_TO {relation_type: r.relation_type}]->(tgt:Entity {name: $target, user_id: $user_id})
                WITH r, existing, tgt, other
                DELETE r
                FOREACH (_ IN CASE WHEN existing IS NULL THEN [1] ELSE [] END |
                    CREATE (other)-[new:RELATES_TO {relation_type: r.relation_type}]->(tgt)
                    SET new = properties(r)
                )
                RETURN count(r) AS removed
                """,
                source=source_name, target=target_name, user_id=user_id,
            )
            record = await result.single()
            incoming_rewritten = int(record["removed"]) if record else 0

            # 4. Delete the now-orphan source entity.
            result = await session.run(
                """
                MATCH (e:Entity {name: $source, user_id: $user_id})
                WITH e, count(e) AS c
                DELETE e
                RETURN c AS deleted
                """,
                source=source_name, user_id=user_id,
            )
            record = await result.single()
            nodes_deleted = int(record["deleted"]) if record else 0

        return {
            "merged_from": source_name,
            "merged_into": target_name,
            "mentions_rewritten": mentions_rewritten,
            "outgoing_relations_rewritten": outgoing_rewritten,
            "incoming_relations_rewritten": incoming_rewritten,
            "source_deleted": nodes_deleted,
        }

    async def create_entities_batch(self, entities: List[Dict[str, Any]], user_id: int) -> int:
        """Bulk upsert entities using UNWIND. Returns the number of rows processed.

        Each entity dict must contain: name, type, description (may be None).
        """
        if not entities:
            return 0
        async with self.session() as session:
            result = await session.run("""
                UNWIND $entities AS ent
                MERGE (e:Entity {name: ent.name, user_id: $user_id})
                SET e.type = ent.type,
                    e.description = COALESCE(ent.description, e.description),
                    e.updated_at = datetime()
                RETURN count(e) AS upserted
            """, entities=entities, user_id=user_id)
            record = await result.single()
            return record["upserted"] if record else 0

    async def link_chunk_to_entity(self, chunk_id: str, entity_name: str, user_id: int):
        """Create MENTIONS link from chunk to entity."""
        async with self.session() as session:
            await session.run("""
                MERGE (c:Chunk {chunk_id: $chunk_id})
                MERGE (e:Entity {name: $entity_name, user_id: $user_id})
                MERGE (c)-[:MENTIONS]->(e)
            """, chunk_id=chunk_id, entity_name=entity_name, user_id=user_id)

    async def link_chunks_to_entities_batch(
        self, links: List[Dict[str, str]], user_id: int
    ) -> int:
        """Bulk create MENTIONS links using UNWIND.

        Each link dict must contain: chunk_id, entity_name.
        """
        if not links:
            return 0
        async with self.session() as session:
            result = await session.run("""
                UNWIND $links AS link
                MERGE (c:Chunk {chunk_id: link.chunk_id})
                MERGE (e:Entity {name: link.entity_name, user_id: $user_id})
                MERGE (c)-[:MENTIONS]->(e)
                RETURN count(*) AS linked
            """, links=links, user_id=user_id)
            record = await result.single()
            return record["linked"] if record else 0

    async def create_relation(self, source: str, target: str, relation_type: str,
                              user_id: int, properties: Dict[str, Any] = None):
        """Create a relation between two entities."""
        props = properties or {}
        async with self.session() as session:
            await session.run("""
                MATCH (s:Entity {name: $source, user_id: $user_id})
                MATCH (t:Entity {name: $target, user_id: $user_id})
                MERGE (s)-[r:RELATES_TO {relation_type: $relation_type}]->(t)
                SET r += $properties, r.updated_at = datetime()
            """, source=source, target=target, relation_type=relation_type,
                user_id=user_id, properties=props)

    async def create_relations_batch(
        self, relations: List[Dict[str, Any]], user_id: int
    ) -> int:
        """Bulk create RELATES_TO edges using UNWIND.

        Each relation dict must contain: source, target, relation_type.
        Optional: properties (dict).
        """
        if not relations:
            return 0
        async with self.session() as session:
            result = await session.run("""
                UNWIND $relations AS rel
                MATCH (s:Entity {name: rel.source, user_id: $user_id})
                MATCH (t:Entity {name: rel.target, user_id: $user_id})
                MERGE (s)-[r:RELATES_TO {relation_type: rel.relation_type}]->(t)
                SET r += coalesce(rel.properties, {}), r.updated_at = datetime()
                RETURN count(r) AS linked
            """, relations=relations, user_id=user_id)
            record = await result.single()
            return record["linked"] if record else 0

    async def search_entities(self, query: str, user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
        """Search entities by name (case-insensitive)."""
        print(f"   Neo4j search: query='{query}', user_id={user_id}")
        async with self.session() as session:
            # First check all entities for this user
            check_result = await session.run("""
                MATCH (e:Entity)
                WHERE e.user_id = $user_id
                RETURN count(e) as total
            """, user_id=user_id)
            check_record = await check_result.single()
            total_entities = check_record["total"] if check_record else 0
            print(f"   Total entities for user {user_id}: {total_entities}")

            # Now search
            result = await session.run("""
                MATCH (e:Entity)
                WHERE e.user_id = $user_id AND toLower(e.name) CONTAINS toLower($search_term)
                RETURN e.name as name, e.type as type, coalesce(e.description, "") as description
                LIMIT $limit
            """, search_term=query, user_id=user_id, limit=limit)
            entities = [record.data() async for record in result]
            print(f"   Found {len(entities)} matching entities")
            return entities

    async def get_related_entities(self, entity_names: List[str], user_id: int,
                                    depth: int = 2) -> Dict[str, Any]:
        """Get entities related to given entities within specified depth."""
        # Cypher variable-length pattern bounds cannot be parameterized, so we
        # clamp to a strict whitelist before composing the query.
        if depth not in (1, 2, 3):
            depth = 2

        query = f"""
            MATCH (center:Entity)
            WHERE center.user_id = $user_id AND center.name IN $entity_names
            OPTIONAL MATCH path = (center)-[:RELATES_TO*1..{depth}]-(related:Entity)
            WHERE related.user_id = $user_id
            RETURN center, related,
                   [node in nodes(path) | {{name: node.name, type: node.type}}] as path_nodes,
                   [rel in relationships(path) | {{type: rel.relation_type}}] as path_rels
            LIMIT 100
        """

        async with self.session() as session:
            result = await session.run(query, entity_names=entity_names, user_id=user_id)

            center_nodes = {}
            related_nodes = {}
            relations = {}

            async for record in result:
                center = record["center"]
                related = record["related"]

                if center:
                    center_nodes[center["name"]] = {
                        "name": center["name"],
                        "type": center["type"],
                        "description": center.get("description", "")
                    }

                if related:
                    related_nodes[related["name"]] = {
                        "name": related["name"],
                        "type": related["type"],
                        "description": related.get("description", "")
                    }

                # Extract relations from path
                path_rels = record.get("path_rels") or []
                if path_rels:
                    for rel in path_rels:
                        rel_key = f"{center['name']}-{rel['type']}-{related['name']}"
                        relations[rel_key] = {
                            "source": center["name"],
                            "target": related["name"],
                            "relation_type": rel["type"]
                        }

            return {
                "center_nodes": list(center_nodes.values()),
                "related_nodes": list(related_nodes.values()),
                "relations": list(relations.values())
            }

    async def get_entities_from_chunks(self, chunk_ids: List[str], user_id: int) -> List[Dict[str, Any]]:
        """Get entities mentioned in chunks."""
        async with self.session() as session:
            result = await session.run("""
                MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
                WHERE c.chunk_id IN $chunk_ids AND c.user_id = $user_id AND e.user_id = $user_id
                RETURN DISTINCT e.name as name, e.type as type, coalesce(e.description, "") as description
            """, chunk_ids=chunk_ids, user_id=user_id)
            return [record.data() async for record in result]

    async def get_user_entities_with_mentions(
        self, user_id: int, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """Return every entity the user has, paired with the chunk_ids and
        document_ids that mention it. Used by the timeline endpoint to
        compute per-entity "first seen" and "mention count" without
        round-tripping per entity.

        The `limit` is a safety cap — we materialise a list of (name,
        type, chunk_ids, document_ids) rows in Python. 200 is generous
        for a single user; if you have more, the endpoint picks the
        most-mentioned ones downstream.

        Schema notes (these have burned us twice — both locked in by
        the test_cypher_uses_correct_directions regression guard):
          * MENTIONS goes **Chunk → Entity**, NOT Entity → Chunk.
            (link_chunk_to_entity creates (Chunk)-[:MENTIONS]->(Entity);
            the reverse direction has no edges and silently matches
            nothing — leading to empty chunk_ids and mention_count=0
            for every entity.)
          * The Chunk→Document link is the
            (:Document)-[:CONTAINS]->(:Chunk) edge, NOT a `document_id`
            property on Chunk. (create_chunk_node never sets it.)
        """
        async with self.session() as session:
            result = await session.run(
                """
                MATCH (e:Entity {user_id: $user_id})
                OPTIONAL MATCH (c:Chunk {user_id: $user_id})-[:MENTIONS]->(e)
                OPTIONAL MATCH (d:Document {user_id: $user_id})-[:CONTAINS]->(c)
                WITH e, collect(c.chunk_id) AS chunk_ids,
                          collect(DISTINCT d.doc_id) AS document_ids
                RETURN e.name AS name,
                       coalesce(e.type, "Unknown") AS type,
                       chunk_ids,
                       document_ids,
                       size(chunk_ids) AS mention_count
                ORDER BY mention_count DESC, name ASC
                LIMIT $limit
                """,
                user_id=user_id,
                limit=limit,
            )
            return [record.data() async for record in result]

    async def count_user_entities(self, user_id: int) -> int:
        """Count how many Entity nodes this user has. Used by the
        dashboard hero stats."""
        async with self.session() as session:
            result = await session.run(
                "MATCH (e:Entity {user_id: $user_id}) RETURN count(e) AS n",
                user_id=user_id,
            )
            record = await result.single()
            return int(record["n"]) if record else 0

    # ---------- Document detail page (top entities, related docs) ---------

    async def get_doc_entities(
        self, doc_id: str, user_id: int, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """Return the most-mentioned entities for a single document.

        Counts MENTIONS edges from this doc's chunks to entities, then
        groups by entity and returns the top `limit` ranked by mention
        count. Both Chunks and Entities are scoped to the user so a doc
        can't reveal info from another tenant's data through this query.

        Traversal note: the Chunk→Document link is the (:Document)
        -[:CONTAINS]->(:Chunk) edge, NOT a `document_id` property on
        Chunk. (The schema is edge-based; `create_chunk_node` never sets
        `c.document_id`.) An earlier revision of this query used
        `(c:Chunk {document_id: $doc_id, ...})` and silently returned
        empty results — a test of mine caught it; see test_c_uses_
        contains_edge_not_property.

        Returns an empty list if the doc has no chunks or no entities.
        """
        async with self.session() as session:
            result = await session.run(
                """
                MATCH (d:Document {doc_id: $doc_id, user_id: $user_id})
                      -[:CONTAINS]->(c:Chunk {user_id: $user_id})
                      -[:MENTIONS]->(e:Entity {user_id: $user_id})
                WITH e.name AS name, e.type AS type, count(c) AS mention_count
                RETURN name, type, mention_count
                ORDER BY mention_count DESC, name ASC
                LIMIT $limit
                """,
                doc_id=doc_id,
                user_id=user_id,
                limit=limit,
            )
            return [record.data() async for record in result]

    async def get_related_documents(
        self, doc_id: str, user_id: int, limit: int = 5
    ) -> List[Dict[str, Any]]:
        """Return other documents that share entities with this one.

        Algorithm:
          1. Find all entities mentioned by the source doc's chunks
             (traversing Document-[:CONTAINS]->Chunk — see get_doc_entities
             for why we use the edge rather than a `document_id` property).
          2. Find other documents that mention any of those same entities.
          3. Rank by the number of distinct shared entities descending.
        """
        async with self.session() as session:
            result = await session.run(
                """
                MATCH (d_src:Document {doc_id: $doc_id, user_id: $user_id})
                      -[:CONTAINS]->(src:Chunk {user_id: $user_id})
                      -[:MENTIONS]->(e:Entity {user_id: $user_id})
                MATCH (d_other:Document {user_id: $user_id})
                      -[:CONTAINS]->(other:Chunk {user_id: $user_id})
                      -[:MENTIONS]->(e)
                WHERE d_other.doc_id <> $doc_id
                WITH d_other, count(DISTINCT e) AS shared_count
                RETURN d_other.doc_id AS doc_id,
                       coalesce(d_other.title, d_other.doc_id) AS title,
                       shared_count
                ORDER BY shared_count DESC, d_other.title ASC
                LIMIT $limit
                """,
                doc_id=doc_id,
                user_id=user_id,
                limit=limit,
            )
            return [record.data() async for record in result]

    async def count_user_relations(self, user_id: int) -> int:
        """Count RELATES_TO edges between the user's entities. Used by
        the dashboard hero stats."""
        async with self.session() as session:
            result = await session.run(
                """
                MATCH (a:Entity {user_id: $user_id})
                      -[r:RELATES_TO]->
                      (b:Entity {user_id: $user_id})
                RETURN count(r) AS n
                """,
                user_id=user_id,
            )
            record = await result.single()
            return int(record["n"]) if record else 0

    async def get_entity_detail(
        self,
        name: str,
        user_id: int,
        sample_chunk_limit: int = 5,
        related_limit: int = 50,
    ) -> Optional[Dict[str, Any]]:
        """One-shot detail page for an entity. Returns a flat envelope:

          {
            "entity":   {name, type, description},
            "stats":    {mention_count, document_count, related_entity_count},
            "documents":      [{doc_id, title, chunk_count, first_seen}, ...]
            "related_entities":[{name, type, relation_type, direction}, ...]
            "sample_chunks":  [{chunk_id, doc_id, doc_title, content_preview}, ...]
          }

        Returns None when the entity doesn't exist for this user — the
        caller maps that to a 404. The Cypher is split into four focused
        queries rather than one giant WITH chain because the per-entity
        subqueries have different shapes (aggregations vs. lists) and
        splitting makes each one readable + individually mockable.
        """
        async with self.session() as session:

            # 1. The entity itself. None if missing.
            result = await session.run(
                """
                MATCH (e:Entity {name: $name, user_id: $user_id})
                RETURN e.name AS name,
                       coalesce(e.type, "OTHER") AS type,
                       coalesce(e.description, "") AS description
                """,
                name=name, user_id=user_id,
            )
            record = await result.single()
            if not record:
                return None
            entity = {
                "name": record["name"],
                "type": record["type"],
                "description": record["description"],
            }

            # 2. Stats: how many chunks mention this entity, how many
            #    distinct docs, how many 1-hop neighbors.
            #    Schema note: Chunk has no `document_id` property — the
            #    link to Document is the `[:CONTAINS]` edge. An earlier
            #    revision of this query used `c.document_id` and
            #    silently returned 0 for every entity.
            result = await session.run(
                """
                MATCH (e:Entity {name: $name, user_id: $user_id})
                OPTIONAL MATCH (c:Chunk {user_id: $user_id})-[:MENTIONS]->(e)
                OPTIONAL MATCH (d:Document {user_id: $user_id})-[:CONTAINS]->(c)
                WITH e, count(DISTINCT c) AS mention_count,
                          count(DISTINCT d) AS document_count
                OPTIONAL MATCH (e)-[r:RELATES_TO]-(:Entity {user_id: $user_id})
                WITH mention_count, document_count, count(r) AS related_entity_count
                RETURN mention_count, document_count, related_entity_count
                """,
                name=name, user_id=user_id,
            )
            stats_row = await result.single()
            # Defensive: if the OPTIONAL MATCH chain somehow returns no
            # row (it shouldn't — the first MATCH on `e` guarantees a
            # binding), fall back to all-zeros rather than 500ing.
            if stats_row is None:
                stats = {
                    "mention_count": 0,
                    "document_count": 0,
                    "related_entity_count": 0,
                }
            else:
                stats = {
                    "mention_count": int(stats_row["mention_count"] or 0),
                    "document_count": int(stats_row["document_count"] or 0),
                    "related_entity_count": int(stats_row["related_entity_count"] or 0),
                }

            # 3. Documents that mention this entity, with chunk counts.
            #    `first_seen` is left as None here — the API layer
            #    hydrates it from SQLite's documents table, which is the
            #    source of truth for created_at.
            result = await session.run(
                """
                MATCH (c:Chunk {user_id: $user_id})-[:MENTIONS]->(e:Entity {name: $name, user_id: $user_id})
                MATCH (d:Document {user_id: $user_id})-[:CONTAINS]->(c)
                WITH d.doc_id AS doc_id,
                     d.title AS title,
                     count(c) AS chunk_count
                RETURN doc_id, title, chunk_count
                ORDER BY chunk_count DESC, doc_id ASC
                """,
                name=name, user_id=user_id,
            )
            documents = [
                {
                    "doc_id": rec["doc_id"],
                    "title": rec["title"] or rec["doc_id"],
                    "chunk_count": int(rec["chunk_count"]),
                    "first_seen": None,
                }
                async for rec in result
            ]

            # 4. 1-hop neighbors — split into outgoing and incoming so
            #    the `direction` tag is accurate. UNION merges them.
            #    relation_type may be null on degenerate edges; we fall
            #    back to "RELATES_TO" so the front-end never has to
            #    render `None`.
            result = await session.run(
                """
                MATCH (e:Entity {name: $name, user_id: $user_id})-[r:RELATES_TO]->(other:Entity {user_id: $user_id})
                RETURN other.name AS name,
                       coalesce(other.type, "OTHER") AS type,
                       coalesce(r.relation_type, "RELATES_TO") AS relation_type,
                       "outgoing" AS direction
                UNION
                MATCH (other:Entity {user_id: $user_id})-[r:RELATES_TO]->(e:Entity {name: $name, user_id: $user_id})
                RETURN other.name AS name,
                       coalesce(other.type, "OTHER") AS type,
                       coalesce(r.relation_type, "RELATES_TO") AS relation_type,
                       "incoming" AS direction
                LIMIT $related_limit
                """,
                name=name, user_id=user_id, related_limit=related_limit,
            )
            related_entities = [
                {
                    "name": rec["name"],
                    "type": rec["type"],
                    "relation_type": rec["relation_type"],
                    "direction": rec["direction"],
                }
                async for rec in result
            ]

            # 5. Sample chunks — up to N representative chunks that
            #    mention this entity, with a 240-char preview.
            result = await session.run(
                """
                MATCH (c:Chunk {user_id: $user_id})-[:MENTIONS]->(e:Entity {name: $name, user_id: $user_id})
                OPTIONAL MATCH (d:Document {user_id: $user_id})-[:CONTAINS]->(c)
                RETURN c.chunk_id AS chunk_id,
                       d.doc_id AS doc_id,
                       coalesce(d.title, d.doc_id) AS doc_title,
                       substring(coalesce(c.content, ""), 0, 240) AS content_preview
                LIMIT $sample_chunk_limit
                """,
                name=name, user_id=user_id, sample_chunk_limit=sample_chunk_limit,
            )
            sample_chunks = [
                {
                    "chunk_id": rec["chunk_id"],
                    "doc_id": rec["doc_id"],
                    "doc_title": rec["doc_title"],
                    "content_preview": rec["content_preview"],
                }
                async for rec in result
            ]

        return {
            "entity": entity,
            "stats": stats,
            "documents": documents,
            "related_entities": related_entities,
            "sample_chunks": sample_chunks,
        }

    # ---------- Graph-RAG (chunk retrieval via entity graph) -----------

    async def get_chunks_for_entities(
        self,
        entity_names: List[str],
        user_id: int,
        limit: int = 100,
    ) -> List[str]:
        """Return chunk_ids that MENTION any of the given entity names.

        Inverse of `get_entities_from_chunks`: given entities, find the
        chunks that talk about them. Used by the graph-RAG retrieval path
        to build a hard-filtered candidate set before the reranker.

        Empty / unknown entity names return [] rather than raising — the
        caller (build_rag_context) is expected to fall back to vector
        retrieval in that case.
        """
        if not entity_names:
            return []
        async with self.session() as session:
            result = await session.run(
                """
                MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
                WHERE e.name IN $entity_names
                  AND e.user_id = $user_id
                  AND c.user_id = $user_id
                RETURN DISTINCT c.chunk_id AS chunk_id
                LIMIT $limit
                """,
                entity_names=list(entity_names),
                user_id=user_id,
                limit=limit,
            )
            return [record["chunk_id"] async for record in result]

    async def get_entity_graph_for_visualization(self, query: str, user_id: int,
                                                  limit: int = 50) -> Dict[str, Any]:
        """Get graph data for visualization starting from query."""
        async with self.session() as session:
            # Find matching entities
            result = await session.run("""
                MATCH (e:Entity)
                WHERE e.user_id = $user_id AND toLower(e.name) CONTAINS toLower($query)
                RETURN e.name as name, e.type as type
                LIMIT 10
            """, query=query, user_id=user_id)

            start_nodes = [record.data() async for record in result]

            if not start_nodes:
                return {"nodes": [], "edges": []}

            start_names = [n["name"] for n in start_nodes]

            # Get related graph
            result = await session.run("""
                MATCH (start:Entity)-[r:RELATES_TO]-(other:Entity)
                WHERE start.user_id = $user_id AND start.name IN $start_names
                AND other.user_id = $user_id
                RETURN start, r, other
                LIMIT $limit
            """, start_names=start_names, user_id=user_id, limit=limit)

            nodes = {}
            edges = []

            async for record in result:
                start = record["start"]
                other = record["other"]
                rel = record["r"]

                nodes[start["name"]] = {
                    "id": start["name"],
                    "type": "Entity",
                    "label": start["name"],
                    "properties": {"type": start["type"]}
                }

                nodes[other["name"]] = {
                    "id": other["name"],
                    "type": "Entity",
                    "label": other["name"],
                    "properties": {"type": other["type"]}
                }

                edges.append({
                    "id": f"{start['name']}-{rel['relation_type']}-{other['name']}",
                    "source": start["name"],
                    "target": other["name"],
                    "label": rel["relation_type"],
                    "type": "RELATES_TO"
                })

            return {
                "nodes": list(nodes.values()),
                "edges": edges
            }

    async def get_full_graph_for_visualization(self, user_id: int) -> Dict[str, Any]:
        """Get complete graph with ALL nodes and relationships for visualization."""
        print(f"   Getting full graph for user {user_id}")
        async with self.session() as session:
            nodes = {}
            edges = []

            # Get all Entity nodes for this user
            result = await session.run("""
                MATCH (e:Entity)
                WHERE e.user_id = $user_id
                RETURN e.name as name, e.type as type, e.description as description
                LIMIT 2000
            """, user_id=user_id)

            async for record in result:
                name = record["name"]
                description = record.get("description", "") or ""
                nodes[f"entity_{name}"] = {
                    "id": f"entity_{name}",
                    "type": "Entity",
                    "label": name,
                    # 顶层 entity_type/description — 与 /graph/query 响应一致，前端直接读取
                    "entity_type": record["type"],
                    "description": description,
                    "is_center": False,
                    "is_highlighted": False,
                    "properties": {"entity_type": record["type"], "description": description}
                }

            # Get all Document nodes for this user
            result = await session.run("""
                MATCH (d:Document)
                WHERE d.user_id = $user_id
                RETURN d.doc_id as doc_id, d.title as title
                LIMIT 500
            """, user_id=user_id)

            async for record in result:
                doc_id = record["doc_id"]
                nodes[f"doc_{doc_id}"] = {
                    "id": f"doc_{doc_id}",
                    "type": "Document",
                    "label": record["title"] or doc_id,
                    "properties": {"title": record["title"], "doc_id": doc_id}
                }

            # Get all Chunk nodes for this user
            result = await session.run("""
                MATCH (c:Chunk)
                WHERE c.user_id = $user_id
                RETURN c.chunk_id as chunk_id, c.hierarchy_path as hierarchy_path, c.position as position
                LIMIT 2000
            """, user_id=user_id)

            async for record in result:
                chunk_id = record["chunk_id"]
                hierarchy = record.get("hierarchy_path") or []
                label = "/".join(hierarchy) if hierarchy else chunk_id[:8]
                nodes[f"chunk_{chunk_id}"] = {
                    "id": f"chunk_{chunk_id}",
                    "type": "Chunk",
                    "label": label,
                    "properties": {"hierarchy_path": hierarchy, "position": record.get("position", 0)}
                }

            # Get User node for this user
            result = await session.run("""
                MATCH (u:User)
                WHERE u.user_id = $user_id
                RETURN u.user_id as user_id
                LIMIT 1
            """, user_id=user_id)

            async for record in result:
                nodes[f"user_{record['user_id']}"] = {
                    "id": f"user_{record['user_id']}",
                    "type": "User",
                    "label": f"User_{record['user_id']}",
                    "properties": {"user_id": record['user_id']}
                }

            print(f"   Found {len(nodes)} total nodes")

            # Get all relationships between these nodes
            # OWNS: (User)-[:OWNS]->(Document)
            result = await session.run("""
                MATCH (u:User)-[r:OWNS]->(d:Document)
                WHERE u.user_id = $user_id AND d.user_id = $user_id
                RETURN u.user_id as uid, d.doc_id as doc_id
                LIMIT 1000
            """, user_id=user_id)

            async for record in result:
                edges.append({
                    "id": f"owns_{record['uid']}_{record['doc_id']}",
                    "source": f"user_{record['uid']}",
                    "target": f"doc_{record['doc_id']}",
                    "label": "OWNS",
                    "type": "OWNS"
                })

            # CONTAINS: (Document)-[:CONTAINS]->(Chunk)
            result = await session.run("""
                MATCH (d:Document)-[r:CONTAINS]->(c:Chunk)
                WHERE d.user_id = $user_id AND c.user_id = $user_id
                RETURN d.doc_id as doc_id, c.chunk_id as chunk_id
                LIMIT 2000
            """, user_id=user_id)

            async for record in result:
                edges.append({
                    "id": f"contains_{record['doc_id']}_{record['chunk_id']}",
                    "source": f"doc_{record['doc_id']}",
                    "target": f"chunk_{record['chunk_id']}",
                    "label": "CONTAINS",
                    "type": "CONTAINS"
                })

            # NEXT: (Chunk)-[:NEXT]->(Chunk)
            result = await session.run("""
                MATCH (c1:Chunk)-[r:NEXT]->(c2:Chunk)
                WHERE c1.user_id = $user_id AND c2.user_id = $user_id
                RETURN c1.chunk_id as source_id, c2.chunk_id as target_id
                LIMIT 2000
            """, user_id=user_id)

            async for record in result:
                edges.append({
                    "id": f"next_{record['source_id']}_{record['target_id']}",
                    "source": f"chunk_{record['source_id']}",
                    "target": f"chunk_{record['target_id']}",
                    "label": "NEXT",
                    "type": "NEXT"
                })

            # MENTIONS: (Chunk)-[:MENTIONS]->(Entity)
            result = await session.run("""
                MATCH (c:Chunk)-[r:MENTIONS]->(e:Entity)
                WHERE c.user_id = $user_id AND e.user_id = $user_id
                RETURN c.chunk_id as chunk_id, e.name as entity_name
                LIMIT 5000
            """, user_id=user_id)

            async for record in result:
                edges.append({
                    "id": f"mentions_{record['chunk_id']}_{record['entity_name']}",
                    "source": f"chunk_{record['chunk_id']}",
                    "target": f"entity_{record['entity_name']}",
                    "label": "MENTIONS",
                    "type": "MENTIONS"
                })

            # RELATES_TO: (Entity)-[:RELATES_TO]->(Entity)
            result = await session.run("""
                MATCH (e1:Entity)-[r:RELATES_TO]->(e2:Entity)
                WHERE e1.user_id = $user_id AND e2.user_id = $user_id
                RETURN e1.name as source, e2.name as target, r.relation_type as relation_type
                LIMIT 5000
            """, user_id=user_id)

            async for record in result:
                edges.append({
                    "id": f"relates_{record['source']}_{record['relation_type']}_{record['target']}",
                    "source": f"entity_{record['source']}",
                    "target": f"entity_{record['target']}",
                    "label": record["relation_type"] or "RELATES_TO",
                    "type": "RELATES_TO"
                })

            print(f"   Found {len(edges)} total relationships")

            return {
                "nodes": list(nodes.values()),
                "edges": edges
            }

    async def delete_user_data(self, user_id: int):
        """Delete all data for a user."""
        print(f"[neo4j] Deleting all data for user {user_id}")
        async with self.session() as session:
            # Delete all relations first
            await session.run("""
                MATCH (e1:Entity)-[r:RELATES_TO]->(e2:Entity)
                WHERE e1.user_id = $user_id OR e2.user_id = $user_id
                DELETE r
            """, user_id=user_id)

            # Delete all entities
            await session.run("""
                MATCH (e:Entity)
                WHERE e.user_id = $user_id
                DETACH DELETE e
            """, user_id=user_id)

            # Delete all chunks
            await session.run("""
                MATCH (c:Chunk)
                WHERE c.user_id = $user_id
                DETACH DELETE c
            """, user_id=user_id)

            # Delete all documents
            await session.run("""
                MATCH (d:Document)
                WHERE d.user_id = $user_id
                DETACH DELETE d
            """, user_id=user_id)

            print(f"[neo4j] All data deleted for user {user_id}")

    async def delete_document(self, doc_id: str, user_id: int):
        """Delete a document and its chunks, entities, and relations."""
        print(f"[neo4j] ====== DELETE START: doc_id={doc_id}, user_id={user_id} ======")

        async with self.session() as session:
            # First, let's see what's currently in the database
            result = await session.run("""
                MATCH (e:Entity)
                WHERE e.user_id = $user_id
                RETURN count(e) as count
            """, user_id=user_id)
            record = await result.single()
            print(f"[neo4j] BEFORE DELETE: Total entities in DB: {record['count'] if record else 0}")

            # Check how many chunks exist for this user
            result = await session.run("""
                MATCH (c:Chunk)
                WHERE c.user_id = $user_id
                RETURN count(c) as count
            """, user_id=user_id)
            record = await result.single()
            print(f"[neo4j] BEFORE DELETE: Total chunks in DB: {record['count'] if record else 0}")

            # Step 1: Check if Document exists, collect chunk IDs
            result = await session.run("""
                MATCH (d:Document {doc_id: $doc_id})
                OPTIONAL MATCH (d)-[:CONTAINS]->(c:Chunk)
                RETURN d, collect(DISTINCT c.chunk_id) as chunk_ids
            """, doc_id=doc_id)
            record = await result.single()
            doc_exists = record and record.get("d") is not None
            chunk_ids = record["chunk_ids"] if record else []

            if not doc_exists:
                print(f"[neo4j] Document {doc_id} not found in Neo4j!")
                # Still print stats
                result = await session.run("""
                    MATCH (e:Entity)
                    WHERE e.user_id = $user_id
                    RETURN count(e) as count
                """, user_id=user_id)
                record = await result.single()
                print(f"[neo4j] AFTER DELETE: Total entities: {record['count'] if record else 0}")
                return
            else:
                print(f"[neo4j] Found document with {len(chunk_ids)} chunks")

            # Step 2: Collect entity names that are mentioned in this document's chunks
            result = await session.run("""
                MATCH (c:Chunk)-[:MENTIONS]->(e:Entity)
                WHERE c.chunk_id IN $chunk_ids AND c.user_id = $user_id
                RETURN collect(DISTINCT e.name) as entity_names
            """, chunk_ids=chunk_ids, user_id=user_id)
            record = await result.single()
            entity_names = record["entity_names"] if record else []
            print(f"[neo4j] Entities in THIS document: {len(entity_names)}")

            # Step 3: Delete MENTIONS relations from chunks
            if chunk_ids:
                result = await session.run("""
                    MATCH (c:Chunk)-[r:MENTIONS]->(e:Entity)
                    WHERE c.chunk_id IN $chunk_ids
                    DELETE r
                    RETURN count(r) as deleted
                """, chunk_ids=chunk_ids)
                record = await result.single()
                print(f"[neo4j] Step 3: Deleted {record['deleted'] if record else 0} MENTIONS relations")

            # Step 4: Delete entities that were IN THIS DOCUMENT only if no
            # remaining chunk (of the SAME user) still mentions them. The
            # optional match MUST be filtered by user_id - otherwise a
            # mention from another user would block the delete (data leak)
            # and one orphan entity could produce multiple rows.
            if entity_names:
                result = await session.run("""
                    MATCH (e:Entity)
                    WHERE e.user_id = $user_id AND e.name IN $entity_names
                    OPTIONAL MATCH (e)<-[r:MENTIONS]-(c:Chunk)
                    WHERE c.user_id = $user_id
                    WITH e, collect(DISTINCT c.chunk_id) AS referencing_chunks
                    WHERE size(referencing_chunks) = 0
                    DETACH DELETE e
                    RETURN count(*) AS deleted
                """, user_id=user_id, entity_names=entity_names)
                record = await result.single()
                logger.info("Step 4: Deleted %d orphaned entities", record["deleted"] if record else 0)

            # Step 5: Delete RELATES_TO relations ONLY for entities that were in this document
            if entity_names:
                result = await session.run("""
                    MATCH (e1:Entity)-[r:RELATES_TO]->(e2:Entity)
                    WHERE e1.user_id = $user_id AND e2.user_id = $user_id
                    AND (e1.name IN $entity_names OR e2.name IN $entity_names)
                    DELETE r
                    RETURN count(r) as deleted
                """, user_id=user_id, entity_names=entity_names)
                record = await result.single()
                print(f"[neo4j] Step 5: Deleted {record['deleted'] if record else 0} RELATES_TO relations")

            # Step 6: Delete Document and Chunk nodes
            result = await session.run("""
                MATCH (d:Document {doc_id: $doc_id})
                OPTIONAL MATCH (d)-[:CONTAINS]->(c:Chunk)
                DETACH DELETE d, c
                RETURN count(d) as deleted
            """, doc_id=doc_id)
            record = await result.single()
            print(f"[neo4j] Step 6: Deleted {record['deleted'] if record else 0} documents")

            # Final stats
            result = await session.run("""
                MATCH (e:Entity)
                WHERE e.user_id = $user_id
                RETURN count(e) as count
            """, user_id=user_id)
            record = await result.single()
            print(f"[neo4j] ====== DELETE COMPLETE: Remaining entities: {record['count'] if record else 0} ======")


# Singleton instance
_neo4j_client: Optional[Neo4jClient] = None


async def get_neo4j_client() -> Neo4jClient:
    """Get singleton Neo4j client instance."""
    global _neo4j_client
    if _neo4j_client is None:
        _neo4j_client = Neo4jClient()
        await _neo4j_client.connect()
    return _neo4j_client
