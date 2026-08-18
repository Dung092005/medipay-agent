from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from src.config import get_settings


class EmbeddingModel(Protocol):
    async def embed_query(self, text: str) -> Sequence[float]:
        """Return embedding vector for text."""


class OpenAIEmbeddingModel:
    def __init__(self, api_key: str, model: str, dimensions: int):
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.dimensions = dimensions

    async def embed_query(self, text: str) -> Sequence[float]:
        response = await self.client.embeddings.create(
            model=self.model, input=text, dimensions=self.dimensions
        )
        return response.data[0].embedding


class UnconfiguredEmbeddingModel:
    async def embed_query(self, text: str) -> Sequence[float]:
        raise RuntimeError("OPENAI_API_KEY is required for text-embedding-3-small")


def get_embedding_model() -> EmbeddingModel:
    settings = get_settings()
    api_key = settings.embedding_api_key or settings.openai_api_key
    if not api_key:
        return UnconfiguredEmbeddingModel()
    return OpenAIEmbeddingModel(api_key, settings.embedding_model, settings.embedding_dimensions)
