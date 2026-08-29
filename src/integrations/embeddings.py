from __future__ import annotations

from collections.abc import Sequence
from functools import lru_cache
from typing import Protocol

from src.config import get_settings


class EmbeddingModel(Protocol):
    async def embed_query(self, text: str) -> Sequence[float]:
        """Return embedding vector for text."""

    async def embed_queries(self, texts: Sequence[str]) -> list[Sequence[float]]:
        """Return vectors in the same order for a bounded sub-query batch."""


class OpenAIEmbeddingModel:
    def __init__(self, api_key: str, model: str, dimensions: int, base_url: str = ""):
        import httpx
        from openai import AsyncOpenAI

        default_headers = {
            "HTTP-Referer": "https://medipay-ai.vercel.app",
            "X-Title": "MediPay BHYT Agent",
        }
        http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )
        kwargs: dict = {
            "api_key": api_key,
            "default_headers": default_headers,
            "http_client": http_client,
            "max_retries": 3,
        }
        if base_url:
            kwargs["base_url"] = base_url
        self.client = AsyncOpenAI(**kwargs)
        self.model = model
        self.dimensions = dimensions

    async def embed_query(self, text: str) -> Sequence[float]:
        import asyncio
        for attempt in range(3):
            try:
                response = await self.client.embeddings.create(
                    model=self.model, input=text, dimensions=self.dimensions
                )
                return response.data[0].embedding
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (2 ** attempt))

    async def embed_queries(self, texts: Sequence[str]) -> list[Sequence[float]]:
        import asyncio
        values = list(texts)
        if not values:
            return []
        for attempt in range(3):
            try:
                response = await self.client.embeddings.create(
                    model=self.model, input=values, dimensions=self.dimensions
                )
                ordered = sorted(response.data, key=lambda item: int(item.index))
                return [item.embedding for item in ordered]
            except Exception as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.5 * (2 ** attempt))


class UnconfiguredEmbeddingModel:
    async def embed_query(self, text: str) -> Sequence[float]:
        raise RuntimeError("OPENAI_API_KEY is required for text-embedding-3-small")

    async def embed_queries(self, texts: Sequence[str]) -> list[Sequence[float]]:
        raise RuntimeError("OPENAI_API_KEY is required for text-embedding-3-small")


@lru_cache(maxsize=1)
def get_embedding_model() -> EmbeddingModel:
    settings = get_settings()
    api_key = (settings.embedding_api_key or settings.openai_api_key).strip()
    if not api_key:
        return UnconfiguredEmbeddingModel()

    base_url = (settings.openai_base_url or "").strip().strip("\"'")
    model = settings.embedding_model.strip()

    # Automatically route OpenRouter keys or recover from dead yescale proxy
    if api_key.startswith("sk-or-") or "yescale" in base_url.lower():
        base_url = "https://openrouter.ai/api/v1"
        if not model.startswith("openai/"):
            model = f"openai/{model}"

    return OpenAIEmbeddingModel(
        api_key,
        model,
        settings.embedding_dimensions,
        base_url=base_url,
    )
