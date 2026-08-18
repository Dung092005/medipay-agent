from __future__ import annotations

from src.db.repositories import GraphRepository
from src.integrations.embeddings import EmbeddingModel
from src.models.graph import Entity, Relation, RetrievalResult


async def retrieve_graph_context(
    query: str,
    entities: list[Entity],
    repository: GraphRepository,
    embeddings: EmbeddingModel,
    top_k: int,
    hops: int,
    neighbor_limit: int,
) -> tuple[list[RetrievalResult], list[Relation]]:
    vector = await embeddings.embed_query(query)
    chunks = await repository.search_vectors(vector, limit=top_k)
    relations = await repository.expand_entities(
        [entity.name for entity in entities], hops=hops, limit=neighbor_limit
    )
    return chunks, relations
