from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.integrations.neo4j import Neo4jGraphStore
from src.models.graph import Relation, RetrievalResult


class GraphRepository:
    """Read-only retrieval boundary over the active PostgreSQL release."""

    def __init__(self, session: AsyncSession, graph_store: Neo4jGraphStore | None = None):
        self.session = session
        self.graph_store = graph_store

    async def current_dataset(self) -> str | None:
        result = await self.session.execute(
            text(
                """
                SELECT d.dataset_id
                FROM dataset_state state
                JOIN datasets d ON d.dataset_id = state.active_dataset_id
                WHERE state.singleton = TRUE AND d.status = 'active'
                """
            )
        )
        dataset_id = result.scalar_one_or_none()
        return str(dataset_id) if dataset_id is not None else None

    async def search_vectors(
        self,
        embedding: Sequence[float],
        *,
        limit: int = 5,
        dataset_id: str | None = None,
        similarity_threshold: float | None = None,
    ) -> list[RetrievalResult]:
        if not embedding:
            return []
        dataset_id = dataset_id or await self.current_dataset()
        if dataset_id is None:
            return []
        threshold = (
            get_settings().semantic_similarity_threshold
            if similarity_threshold is None
            else similarity_threshold
        )
        vector = "[" + ",".join(format(float(value), ".10g") for value in embedding) + "]"
        result = await self.session.execute(
            text(
                """
                SELECT c.chunk_id, c.document_id, c.text, c.section_title,
                       d.title, 1.0 - (c.embedding <=> CAST(:embedding AS extensions.vector)) AS score
                FROM chunks c
                JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                WHERE c.dataset_id = :dataset_id
                  AND c.embedding IS NOT NULL
                  AND c.semantic_eligible IS TRUE
                  AND c.semantic_eligible
                  AND 1.0 - (c.embedding <=> CAST(:embedding AS extensions.vector)) >= :similarity_threshold
                ORDER BY c.embedding <=> CAST(:embedding AS extensions.vector), c.chunk_id
                LIMIT :limit
                """
            ),
            {
                "embedding": vector,
                "dataset_id": dataset_id,
                "limit": limit,
                "similarity_threshold": threshold,
            },
        )

        return [
            RetrievalResult(
                chunk_id=str(row.chunk_id),
                document_id=str(row.document_id),
                content=str(row.text or ""),
                source=str(row.document_id),
                title=str(row.title or ""),
                section_title=str(row.section_title or ""),
                score=max(0.0, float(row.score)),
                channels=["semantic"],
            )
            for row in result
        ]

    async def hydrate_documents(
        self,
        document_ids: Sequence[str],
        *,
        dataset_id: str | None = None,
        chunks_per_document: int = 2,
    ) -> list[RetrievalResult]:
        if not document_ids:
            return []
        dataset_id = dataset_id or await self.current_dataset()
        if dataset_id is None:
            return []
        result = await self.session.execute(
            text(
                """
                SELECT chunk_id, document_id, text, section_title, title
                FROM (
                    SELECT c.chunk_id, c.document_id, c.text, c.section_title,
                           d.title,
                           ROW_NUMBER() OVER (
                               PARTITION BY c.document_id
                               ORDER BY c.chunk_order, c.chunk_id
                           ) AS chunk_rank
                    FROM chunks c
                    JOIN documents d ON d.dataset_id = c.dataset_id AND d.id = c.document_id
                    WHERE c.dataset_id = :dataset_id AND c.document_id = ANY(:document_ids)
                ) ranked
                WHERE chunk_rank <= :chunks_per_document
                ORDER BY document_id, chunk_rank, chunk_id
                """
            ),
            {
                "dataset_id": dataset_id,
                "document_ids": list(document_ids),
                "chunks_per_document": chunks_per_document,
            },
        )
        return [
            RetrievalResult(
                chunk_id=str(row.chunk_id),
                document_id=str(row.document_id),
                content=str(row.text or ""),
                source=str(row.document_id),
                title=str(row.title or ""),
                section_title=str(row.section_title or ""),
                channels=["legal_graph"],
            )
            for row in result
        ]

    async def expand_entities(
        self,
        entity_names: list[str],
        *,
        dataset_id: str | None = None,
        hops: int = 1,
        limit: int = 20,
    ) -> list[Relation]:
        if not entity_names or self.graph_store is None:
            return []
        dataset_id = dataset_id or await self.current_dataset()
        if dataset_id is None:
            return []
        return await self.graph_store.expand(
            entity_names, dataset_id=dataset_id, hops=hops, limit=limit
        )
