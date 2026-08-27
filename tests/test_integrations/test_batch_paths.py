import asyncio

from src.integrations.embeddings import OpenAIEmbeddingModel


def test_embedding_batch_empty_input_is_deterministic() -> None:
    model = OpenAIEmbeddingModel.__new__(OpenAIEmbeddingModel)
    model.client = None
    model.model = "test"
    model.dimensions = 3
    assert asyncio.run(model.embed_queries([])) == []
