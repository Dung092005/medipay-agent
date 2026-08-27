from __future__ import annotations

import warnings

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
    warnings.warn(
        "src.graph_rag.retrieval is retired; use GraphRagRuntime.retrieve instead",
        DeprecationWarning,
        stacklevel=2,
    )
    from src.services.chat import get_runtime

    return await get_runtime().retrieve(query)
