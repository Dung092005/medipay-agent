from __future__ import annotations

from dataclasses import dataclass

from src.graph_rag.chunking import split_document
from src.graph_rag.extraction import GraphExtractor
from src.integrations.embeddings import EmbeddingModel
from src.models.graph import Entity, Relation


@dataclass
class IngestedChunk:
    index: int
    content: str
    embedding: list[float] | None = None


async def prepare_document(
    content: str,
    extractor: GraphExtractor,
    embeddings: EmbeddingModel | None = None,
) -> tuple[list[IngestedChunk], list[Entity], list[Relation]]:
    chunks = split_document(content)
    ingested: list[IngestedChunk] = []
    all_entities: list[Entity] = []
    all_relations: list[Relation] = []

    for index, chunk in enumerate(chunks):
        vector = None
        if embeddings is not None:
            vector = list(await embeddings.embed_query(chunk))
        entities, relations = await extractor.extract(chunk)
        ingested.append(IngestedChunk(index=index, content=chunk, embedding=vector))
        all_entities.extend(entities)
        all_relations.extend(relations)

    return ingested, all_entities, all_relations
