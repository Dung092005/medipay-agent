"""Neo4j persistence adapter for the knowledge graph."""

from __future__ import annotations

from collections.abc import Sequence

from src.config import get_settings
from src.models.graph import Relation


class Neo4jGraphStore:
    def __init__(self) -> None:
        from neo4j import AsyncGraphDatabase

        settings = get_settings()
        if not settings.neo4j_uri or not settings.neo4j_password:
            raise RuntimeError("NEO4J_URI and NEO4J_PASSWORD are required")
        self.database = settings.neo4j_database
        self.driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
        )

    async def verify_connectivity(self) -> None:
        await self.driver.verify_connectivity()

    async def readiness(
        self,
        *,
        dataset_id: str,
        expected_nodes: int | None = None,
        expected_approved_edges: int | None = None,
    ) -> bool:
        """Check release-scoped node/edge counts, not connectivity alone."""
        async with self.driver.session(database=self.database) as session:
            result = await session.run(
                """
                MATCH (n)
                WHERE n.dataset_id = $dataset_id
                WITH count(n) AS node_count
                OPTIONAL MATCH ()-[r]->()
                WHERE r.dataset_id = $dataset_id
                  AND r.serving_status = 'approved_evidence'
                RETURN node_count, count(r) AS relationship_count
                """,
                dataset_id=dataset_id,
            )
            row = await result.single()
        if not row:
            return False
        node_count = int(row["node_count"])
        relationship_count = int(row["relationship_count"])
        if expected_nodes is not None and node_count != expected_nodes:
            return False
        if expected_approved_edges is not None and relationship_count != expected_approved_edges:
            return False
        return node_count > 0 and relationship_count > 0

    async def expand(
        self,
        entity_names: Sequence[str],
        *,
        dataset_id: str,
        hops: int = 1,
        limit: int = 20,
    ) -> list[Relation]:
        if not entity_names or hops < 1:
            return []
        hops = min(hops, 5)
        query = f"""
        MATCH (source:Document)-[path*1..{hops}]->(target:Document)
        WHERE source.dataset_id = $dataset_id
          AND target.dataset_id = $dataset_id
          AND (source.id IN $ids OR target.id IN $ids)
          AND ALL(rel IN path WHERE type(rel) <> 'ALIAS_OF'
              AND rel.serving_status = 'approved_evidence')
        UNWIND path AS rel
        RETURN startNode(rel).id AS source_id,
               startNode(rel).name AS source_name,
               type(rel) AS relation_type,
               endNode(rel).id AS target_id,
               endNode(rel).name AS target_name,
               coalesce(rel.relationship_type, '') AS description,
               coalesce(rel.relationship_id, '') AS relationship_id,
               coalesce(rel.adverse, false) AS adverse,
               CASE WHEN startNode(rel).id IN $ids THEN 'outbound' ELSE 'inbound' END AS direction
        LIMIT $limit
        """
        async with self.driver.session(database=self.database) as session:
            result = await session.run(
                query,
                ids=list(entity_names),
                dataset_id=dataset_id,
                limit=limit,
            )
            rows = await result.data()
        return [
            Relation(
                source=str(row.get("source_name") or row.get("source_id") or ""),
                target=str(row.get("target_name") or row.get("target_id") or ""),
                source_id=str(row.get("source_id") or ""),
                target_id=str(row.get("target_id") or ""),
                relation_type=str(row.get("relation_type") or "RELATED"),
                description=str(row.get("description") or ""),
                relationship_id=str(row.get("relationship_id") or ""),
                adverse=bool(row.get("adverse")),
                direction=str(row.get("direction") or ""),
            )
            for row in rows
        ]

    async def close(self) -> None:
        await self.driver.close()
