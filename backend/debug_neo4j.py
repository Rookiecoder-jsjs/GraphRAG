"""Debug: Check what's in Neo4j"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app.services.neo4j_client import get_neo4j_client


async def check_neo4j_data():
    neo4j = await get_neo4j_client()
    user_id = 1

    async with neo4j.session() as session:
        # Check all Document nodes
        result = await session.run("""
            MATCH (d:Document)
            RETURN d.doc_id as doc_id, d.title as title, d.user_id as user_id
            LIMIT 10
        """)
        docs = [dict(record) async for record in result]
        print(f"Documents in Neo4j: {docs}")

        # Check all Chunk nodes
        result = await session.run("""
            MATCH (c:Chunk)
            WHERE c.user_id = $user_id
            RETURN count(c) as chunk_count, collect(DISTINCT c.user_id) as user_ids
        """, user_id=user_id)
        record = await result.single()
        print(f"Chunks: {record}")

        # Check all Entity nodes
        result = await session.run("""
            MATCH (e:Entity)
            WHERE e.user_id = $user_id
            RETURN count(e) as entity_count
        """, user_id=user_id)
        record = await result.single()
        print(f"Entities: {record}")

        # Check all relations
        result = await session.run("""
            MATCH ()-[r:RELATES_TO]->()
            RETURN count(r) as relation_count
        """)
        record = await result.single()
        print(f"Relations: {record}")


if __name__ == "__main__":
    asyncio.run(check_neo4j_data())
